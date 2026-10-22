"""Request and response models for endpoints 17.1 – 17.3 (docs/API_CONTRACTS.md §17).

**The entire request body is one closed enum plus a free-text reason.** There is no command,
script, argument, document name, timeout override, host or URL field, and none may be added
(mandatory rules 1 and 2); ``extra="forbid"`` makes an unknown field a ``422``, and a test walks
the OpenAPI schema to prove it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.inventory.schemas import CursorPage

#: Printable text only: a reason is audited and rendered, and a control character in either is a
#: log-forging tool.
Reason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=500, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]

ExecutionStatus = Literal[
    "PENDING",
    "AWAITING_APPROVAL",
    "APPROVED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "REJECTED",
]
ApprovalStatus = Literal["NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED", "EXPIRED"]
ApprovalDecisionValue = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
#: §17.1 accepts these three; ``status`` and ``logs_tail`` are served by §16.4 and §16.7.
RequestableAction = Literal["start", "stop", "restart"]


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RequestableAction
    reason: Reason | None = None


class TargetView(BaseModel):
    server_id: uuid.UUID
    server_display_name: str
    service_name: str
    #: The environment stored on the operation when it was requested.
    environment: str


class ActorView(BaseModel):
    id: uuid.UUID
    display_name: str


class ApprovalSummary(BaseModel):
    """The operation's approval request (M4.5); ``null`` on an operation that never needed one."""

    approval_id: uuid.UUID
    #: Always ``1`` in M4.5: a policy asking for more is refused at request time (``OPEN-95``).
    required_approvals: int
    expires_at: datetime
    decision: ApprovalDecisionValue
    required_role: str
    #: The decider, also for a rejection; ``null`` until decided.
    approved_by: ActorView | None = None
    decided_at: datetime | None = None


class OperationBase(BaseModel):
    operation_id: uuid.UUID
    execution_status: ExecutionStatus
    approval_status: ApprovalStatus
    action_type: str
    target: TargetView
    requested_by: ActorView
    requested_reason: str | None
    approval: ApprovalSummary | None = None
    deadline_at: datetime
    created_at: datetime
    correlation_id: uuid.UUID
    #: Seconds to wait before asking again; ``null`` once the operation is terminal.
    poll_after_seconds: int | None


class OperationAccepted(OperationBase):
    """§17.1. ``200`` on an idempotent replay, ``202`` otherwise."""

    idempotent_replay: bool


class OperationDetail(OperationBase):
    """§17.2."""

    observed_state_before: str | None
    observed_state_after: str | None
    #: Derived from ``completed_at`` (no column): set only on ``SUCCEEDED``.
    verified_at: datetime | None
    sanitized_result: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None
    external_execution_id: str | None
    parent_operation_id: uuid.UUID | None
    started_at: datetime | None
    completed_at: datetime | None


class HistoryApproval(BaseModel):
    """The approval reference on a history row (§23): who decided, and when."""

    approval_id: uuid.UUID
    decision: ApprovalDecisionValue
    approved_by: ActorView | None = None
    decided_at: datetime | None = None


class HistoryItem(BaseModel):
    """One row of §23's list.

    Carries the result and the execution id (PRD §4.12) -- ``external_execution_id`` and the two
    observed states are closed values or an opaque provider id. ``requested_reason``,
    ``sanitized_result`` and ``parent_operation_id`` are §17.2's, not the list's.
    """

    operation_id: uuid.UUID
    execution_status: ExecutionStatus
    approval_status: ApprovalStatus
    action_type: str
    target: TargetView
    requested_by: ActorView
    approval: HistoryApproval | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deadline_at: datetime
    failure_reason: str | None
    correlation_id: uuid.UUID
    external_execution_id: str | None
    observed_state_before: str | None
    observed_state_after: str | None


class HistoryList(BaseModel):
    items: list[HistoryItem]
    page: CursorPage
