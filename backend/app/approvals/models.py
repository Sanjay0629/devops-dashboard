"""Approval policy (docs/DATABASE.md §4.7) and the approval requests decided against it (§7).

``ApprovalPolicy`` is an addition beyond PRD §6, which defines ``approvals`` (the decisions) but
no table for the policy those decisions are evaluated against. Mandatory rule 6 -- "never
execute a production action without satisfying its configured approval policy" -- needs
something to evaluate.

``Approval`` (M4.5, migration ``0012``) is **one row per request** (``OPEN-94``): created
``PENDING`` in the request's transaction, then decided, or expired, exactly once. Two schema facts
carry the security meaning and hold whatever the application does:

* ``uq_approvals_one_open_per_operation`` -- at most one open request per operation;
* ``ck_approvals_decider_present`` -- a decided, non-expired row names its decider (the worker
  reads that name as evidence, ``OPEN-99``).

Rows are never deleted. ``operation_id`` has no foreign key (docs/DATABASE.md §15): it points at
``operations`` today and at ``deployments`` in M5.4.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check

ENVIRONMENTS: Final[tuple[str, ...]] = (
    "development",
    "staging",
    "production",
    "other",
)
#: A policy may apply to every environment; a *grant* may not (see
#: docs/SECURITY.md §4 rule 4 — "*" never implies production for permissions).
POLICY_ENVIRONMENTS: Final[tuple[str, ...]] = (*ENVIRONMENTS, "*")
APPROVAL_SUBJECTS: Final[tuple[str, ...]] = ("SERVICE_OPERATION", "DEPLOYMENT")
#: ``approvals.decision``. ``EXPIRED`` is a request nobody decided, or one a cancel withdrew.
APPROVAL_DECISIONS: Final[tuple[str, ...]] = ("PENDING", "APPROVED", "REJECTED", "EXPIRED")


class ApprovalPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approval_policies"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(32), nullable=False)

    required_approvals: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    # PRD §3.6 separation of duties. False by default: a requester approving
    # their own production restart defeats the point of the control.
    allow_self_approval: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    required_role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'APPROVER'")
    )
    # An unapproved request expires rather than lingering as live production
    # intent that someone approves hours later.
    approval_window_seconds: Mapped[int] = mapped_column(
        nullable=False, server_default=text("3600")
    )

    __table_args__ = (
        enum_check("environment", POLICY_ENVIRONMENTS, "environment"),
        enum_check("applies_to", APPROVAL_SUBJECTS, "applies_to"),
        CheckConstraint(
            "required_approvals BETWEEN 1 AND 5",
            name="ck_approval_policies_required_approvals",
        ),
        CheckConstraint(
            "approval_window_seconds > 0",
            name="ck_approval_policies_approval_window_seconds",
        ),
    )


class Approval(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "approvals"

    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: The policy in force when the request was made; ``NULL`` when the built-in defaults applied.
    approval_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_policies.id", ondelete="RESTRICT"), nullable=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: The decider -- also for a rejection (the column keeps its documented name, ``OPEN-94``).
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        enum_check("operation_type", APPROVAL_SUBJECTS, "operation_type"),
        enum_check("decision", APPROVAL_DECISIONS, "decision"),
        CheckConstraint("(decision = 'PENDING') = (decided_at IS NULL)", name="decided_consistent"),
        CheckConstraint(
            "decision IN ('PENDING', 'EXPIRED') OR approved_by IS NOT NULL",
            name="decider_present",
        ),
        Index(
            "uq_approvals_one_decision_per_approver",
            "operation_type",
            "operation_id",
            "approved_by",
            unique=True,
            postgresql_where=text("approved_by IS NOT NULL"),
        ),
        Index(
            "uq_approvals_one_open_per_operation",
            "operation_type",
            "operation_id",
            unique=True,
            postgresql_where=text("decision = 'PENDING'"),
        ),
        Index("ix_approvals_operation", "operation_type", "operation_id"),
        Index("ix_approvals_pending", "decision", postgresql_where=text("decision = 'PENDING'")),
        Index("ix_approvals_expiry", "expires_at", postgresql_where=text("decision = 'PENDING'")),
    )
