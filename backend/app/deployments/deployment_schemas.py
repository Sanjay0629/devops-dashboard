"""Request and response models for §21.1 – §21.3 and §21.6 (``OPEN-139``, ``OPEN-140``).

**The entire request body is an application, an environment, a ref and a reason.** There is no
Jenkins URL, job name, build parameter, node label or credential field, and none may be added
(PRD §4.10, mandatory rules 1 and 9): the pipeline and its parameters come from the registered
target, never from the caller. ``extra="forbid"`` makes an unknown field a ``422``, and a test puts
each forbidden one into every body and query.

Nothing here carries a Jenkins URL, the parameter template, the target digest or the queue item.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.inventory.schemas import CursorPage, Environment
from app.operations.schemas import ActorView, Reason

ExecutionStatus = Literal[
    "REQUESTED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "ROLLED_BACK",
]
ApprovalStatus = Literal["NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"]
Kind = Literal["DEPLOY", "ROLLBACK"]
PipelineResult = Literal["SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"]
#: Length only; the grammar (``app.deployments.refs.is_valid_ref``) is checked by the service so a
#: bad ref is a field error naming the rule, and the patterns only ever see a valid one.
Ref = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    environment: Environment
    ref: Ref
    reason: Reason | None = None


class TargetEcho(BaseModel):
    """The exact deployment target, shown before and after submission (PRD §4.10).

    ``pipeline_reference`` is the job path an administrator registered, as §21.8 shows it to any
    reader; it is not a URL and is not built from anything the caller sent.
    """

    application_id: uuid.UUID
    application_name: str
    environment: str
    pipeline_reference: str
    requested_ref: str
    rollback_supported: bool


class ApprovalPending(BaseModel):
    """The pending approval (M5.4). Always ``null`` in M5.2: nothing is queued behind one."""

    approval_id: uuid.UUID
    required_approvals: int
    expires_at: datetime


class DeploymentAccepted(BaseModel):
    """§21.2. ``200`` on an idempotent replay, ``202`` otherwise."""

    deployment_id: uuid.UUID
    execution_status: ExecutionStatus
    approval_status: ApprovalStatus
    target: TargetEcho
    approval: ApprovalPending | None = None
    deadline_at: datetime
    correlation_id: uuid.UUID
    #: Seconds to wait before asking again; ``null`` once the deployment is terminal.
    poll_after_seconds: int | None
    idempotent_replay: bool


class DeploymentItem(BaseModel):
    """One row of §21.1. No result, no poll error, no template: those are §21.3's."""

    deployment_id: uuid.UUID
    kind: Kind
    application_id: uuid.UUID
    application_name: str
    environment: str
    requested_ref: str
    commit_sha: str | None
    execution_status: ExecutionStatus
    approval_status: ApprovalStatus
    requested_by: ActorView
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deadline_at: datetime
    failure_reason: str | None
    correlation_id: uuid.UUID


class DeploymentList(BaseModel):
    items: list[DeploymentItem]
    page: CursorPage


class DeploymentDetail(DeploymentItem):
    """§21.3."""

    target: TargetEcho
    requested_reason: str | None
    #: The Jenkins build **number**, never a URL.
    external_execution_id: str | None
    pipeline_result: PipelineResult | None
    #: Never ``{}`` on a success: with no checks configured it says so explicitly (``OPEN-138``).
    health_check_result: dict[str, Any] = Field(default_factory=dict)
    sanitized_result: dict[str, Any] = Field(default_factory=dict)
    #: The closed Jenkins reason of the last failed poll, or ``null``.
    last_poll_error: str | None
    last_polled_at: datetime | None
    #: Empty until M5.4.
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    rollback_of_deployment_id: uuid.UUID | None
    poll_after_seconds: int | None
