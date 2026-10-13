"""Approval policy (docs/DATABASE.md §4.7).

An addition beyond PRD §6, which defines ``approvals`` (the decisions) but no
table for the policy those decisions are evaluated against. Mandatory rule 6 —
"never execute a production action without satisfying its configured approval
policy" — needs something to evaluate.

The ``approvals`` table itself arrives in M4.5 with the workflow that writes to
it; only the policy is needed now, because ``service_action_policies``
references it.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import CheckConstraint, String, text
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
