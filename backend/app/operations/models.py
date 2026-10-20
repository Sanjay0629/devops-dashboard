"""``operations`` (docs/DATABASE.md §6): the durable record of every privileged service operation.

Four schema facts carry the security meaning and hold whatever the application does:

* ``uq_operations_idempotency`` -- the same requester and key is one operation (PRD §4.6);
* ``uq_operations_single_inflight`` -- at most one operation in flight per ``(server, service)``,
  a storage guarantee instead of a check that races;
* ``ck_operations_verified_success`` -- a ``SUCCEEDED`` row carries ``observed_state_after``
  (mandatory rule 8; the state machine refuses it too, and each guard is tested alone);
* ``ck_operations_terminal_completed`` -- a terminal row has a completion time.

``VERIFYING`` is never a value of ``execution_status`` (``OPEN-69``). Rows are never deleted.
``target_service_id`` is nullable for future server-level operations, which the single-in-flight
index would **not** guard (PostgreSQL treats NULLs as distinct); none are built.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.approvals.models import ENVIRONMENTS
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check
from app.operations.codes import ACTION_TYPES, APPROVAL_STATUSES
from app.operations.states import IN_FLIGHT, STATUSES, TERMINAL


def _in(values: tuple[str, ...] | frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values) if value)


class Operation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "operations"

    target_server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("servers.id", ondelete="RESTRICT"), nullable=False
    )
    target_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=True
    )
    #: A snapshot of the target's environment at request time: the authorisation and approval
    #: basis, which must not shift if the server is later re-labelled.
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: The provider's id (SSM ``CommandId``...). Written, and committed, **before** polling.
    external_execution_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: The adapter id that executed (or would have executed) it -- not the account's provider.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_state_before: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The evidence for ``SUCCEEDED``.
    observed_state_after: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Closed fields only (``OPEN-74``): adapter_id, steps, observed_*, reconciliation_required,
    #: reason_code. The adapter interface returns no free text.
    sanitized_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    parent_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operations.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    #: Written by the worker on every poll (M4.3, ``OPEN-84``); a quiet one makes a row resumable.
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        enum_check("environment", ENVIRONMENTS, "environment"),
        enum_check("action_type", ACTION_TYPES, "action_type"),
        enum_check("approval_status", APPROVAL_STATUSES, "approval_status"),
        enum_check("execution_status", STATUSES, "execution_status"),
        CheckConstraint(
            "execution_status <> 'SUCCEEDED' OR observed_state_after IS NOT NULL",
            name="verified_success",
        ),
        CheckConstraint(
            f"execution_status NOT IN ({_in(TERMINAL)}) OR completed_at IS NOT NULL",
            name="terminal_completed",
        ),
        UniqueConstraint("requested_by", "idempotency_key", name="uq_operations_idempotency"),
        Index(
            "uq_operations_single_inflight",
            "target_server_id",
            "target_service_id",
            unique=True,
            postgresql_where=text(f"execution_status IN ({_in(IN_FLIGHT)})"),
        ),
        Index("ix_operations_server_created", "target_server_id", text("created_at DESC")),
        Index(
            "ix_operations_waiting",
            "execution_status",
            postgresql_where=text("execution_status IN ('PENDING', 'APPROVED', 'RUNNING')"),
        ),
        Index("ix_operations_requester_created", "requested_by", text("created_at DESC")),
        Index("ix_operations_correlation_id", "correlation_id"),
        Index(
            "ix_operations_deadline",
            "deadline_at",
            postgresql_where=text("completed_at IS NULL"),
        ),
    )
