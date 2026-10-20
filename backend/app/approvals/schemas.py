"""Request and response models for endpoints 22.1 – 22.4 (docs/API_CONTRACTS.md §22).

The decision body is a bounded, printable reason and nothing else: no operation id, no target, no
policy, no flag that could say "approve as someone else". Everything a decision depends on is read
from the database by the id in the path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.inventory.schemas import CursorPage
from app.operations.schemas import ActorView, Reason, TargetView

ApprovalDecision = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]


class DecisionRequest(BaseModel):
    """§22.3 / §22.4. Optional on approve, **required on reject** (checked in the service)."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason | None = None


class ApprovalItem(BaseModel):
    approval_id: uuid.UUID
    operation_id: uuid.UUID
    operation_type: Literal["SERVICE_OPERATION"]
    requested_by: ActorView
    target: TargetView
    action_type: str
    reason: str | None
    decision: ApprovalDecision
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None
    approved_by: ActorView | None
    #: The requester is the caller. Advice for the UI, not a control (§22.3 refuses it again).
    self_requested: bool
    #: The governing policy's ``allow_self_approval``; ``false`` for the built-in defaults.
    self_approval_allowed: bool


class ApprovalList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalItem]
    page: CursorPage


class PolicyView(BaseModel):
    #: ``null`` when the built-in defaults applied (a production ``stop`` with no policy).
    policy_id: uuid.UUID | None
    required_approvals: int
    allow_self_approval: bool
    required_role: str
    approval_window_seconds: int


class DecisionSoFar(BaseModel):
    approved_by: ActorView
    decision: Literal["APPROVED", "REJECTED"]
    reason: str | None
    decided_at: datetime


class TargetState(BaseModel):
    """The service's last observed state. Not a pre-flight read: none is made at request time."""

    state: str
    observed_at: datetime | None
    data_state: Literal["ok", "stale", "unavailable"]


class ApprovalDetail(ApprovalItem):
    policy: PolicyView
    decisions_so_far: list[DecisionSoFar]
    target_state: TargetState
