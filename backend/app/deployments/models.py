"""``deployment_targets`` and ``deployments`` (docs/DATABASE.md §5; ``OPEN-18``, ``OPEN-118`` –
``OPEN-129``, ``OPEN-135``).

A target is a **registered pipeline**: the job path DCP may run for one application in one
environment, the parameters it may send, the refs it may deploy and the approval policy that
gates it. Jenkins itself (base URL, user, token reference) is deployment configuration, not a
column: one Jenkins per deployment, and nothing here can name another.

The database enforces what an application bug must not be able to widen: the job-path grammar,
that the rollback pair travels together, the pattern count, and one live target per application
and environment.

``deployments`` (M5.2) is the durable record of every deployment. Its schema holds, whatever the
application does: one request per requester and key (``uq_deployments_idempotency``); **one active
deployment per application and environment** (``uq_deployments_single_inflight``, a storage
guarantee and not a check that races); a ``SUCCEEDED`` row carries health evidence
(``ck_deployments_success_evidence``, mandatory rule 8, with the state machine as the independent
guard); a running or succeeded row has its build number; a terminal row has a completion time; and
a ``FAILED`` row has a reason and no other row has one. Rows are never deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.approvals.models import ENVIRONMENTS
from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check
from app.deployments.codes import APPROVAL_STATUSES, KINDS
from app.deployments.paths import JOB_PATH_PATTERN
from app.deployments.refs import MAX_PATTERNS
from app.deployments.states import IN_FLIGHT, POLLED, STATUSES, TERMINAL

TARGET_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "DISABLED")
REASON_MAX_LENGTH: Final = 500
REF_MAX_LENGTH: Final = 200


def _in(values: tuple[str, ...] | frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


class DeploymentTarget(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "deployment_targets"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A Jenkins job path. **Never taken from a request** (PRD §4.10).
    pipeline_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    parameter_template: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    allowed_ref_patterns: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    approval_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_policies.id", ondelete="RESTRICT"), nullable=True
    )
    #: ``{}`` until M5.5 defines the schema and the SSRF validation of its URLs (``OPEN-124``).
    health_check_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    rollback_supported: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    rollback_pipeline_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))

    #: The ``applications`` idempotency pattern (docs/API_CONTRACTS.md §7): the key lives on the
    #: row, unique per requesting user, so a replay returns the original resource.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        enum_check("environment", ENVIRONMENTS, "environment"),
        enum_check("status", TARGET_STATUSES, "status"),
        CheckConstraint(f"pipeline_reference ~ '{JOB_PATH_PATTERN}'", name="pipeline_reference"),
        CheckConstraint(
            f"rollback_pipeline_reference IS NULL OR rollback_pipeline_reference ~ "
            f"'{JOB_PATH_PATTERN}'",
            name="rollback_pipeline_reference",
        ),
        CheckConstraint(
            "rollback_supported = (rollback_pipeline_reference IS NOT NULL)",
            name="rollback_pair",
        ),
        CheckConstraint("jsonb_typeof(parameter_template) = 'object'", name="parameter_template"),
        CheckConstraint(
            f"cardinality(allowed_ref_patterns) <= {MAX_PATTERNS}", name="allowed_ref_patterns"
        ),
        Index(
            "uq_deployment_targets_application_id_environment",
            "application_id",
            "environment",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_deployment_targets_created_by_idempotency_key",
            "created_by",
            "idempotency_key",
            unique=True,
            postgresql_where=text("created_by IS NOT NULL AND idempotency_key IS NOT NULL"),
        ),
        Index("ix_deployment_targets_environment", "environment"),
        # The key a deployment's composite foreign key points at (M5.2, ``OPEN-135``).
        UniqueConstraint(
            "id",
            "application_id",
            "environment",
            name="uq_deployment_targets_id_application_id_environment",
        ),
    )


class Deployment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deployments"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    #: Proves the deployment used a registered target -- and, with the composite foreign key
    #: below, that its application and environment are that target's, not merely claimed.
    deployment_target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: The stored environment: the authorisation and approval basis, denormalised from the target.
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A snapshot of the job path actually used; targets change over time.
    pipeline_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Resolved from Jenkins where it reports one.
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Validated against ``allowed_ref_patterns`` before the row exists.
    requested_ref: Mapped[str] = mapped_column(String(REF_MAX_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DEPLOY'"))
    rollback_of_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployments.id"), nullable=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: Required for production or when approval is required; audited by its length only.
    requested_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The Jenkins build number, persisted **before** polling it (AD-08).
    external_execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The Jenkins queue item. A ``QUEUED`` row without one means "the trigger may have been
    #: sent": it is reconciled, never re-triggered.
    queue_item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: SHA-256 of the pipeline reference and template the requester was shown (``OPEN-137``).
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Per-check outcome; **never ``{}`` on a success** -- an explicit "none configured" result.
    health_check_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Closed fields only; a redacted summary, never Jenkins' words.
    sanitized_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: A closed code (``app.deployments.codes``), present exactly when ``FAILED``.
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The closed Jenkins reason of the last failed poll, so an outage is visible, not silent.
    last_poll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    #: When the poller may next look; also its lease. ``NULL`` once finished.
    next_poll_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    #: Consecutive failed polls; drives the backoff and is reset by a success.
    poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deadline_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["deployment_target_id", "application_id", "environment"],
            [
                "deployment_targets.id",
                "deployment_targets.application_id",
                "deployment_targets.environment",
            ],
            name="fk_deployments_deployment_target_id_deployment_targets",
            ondelete="RESTRICT",
        ),
        enum_check("environment", ENVIRONMENTS, "environment"),
        enum_check("execution_status", STATUSES, "execution_status"),
        enum_check("approval_status", APPROVAL_STATUSES, "approval_status"),
        enum_check("kind", KINDS, "kind"),
        CheckConstraint(f"pipeline_reference ~ '{JOB_PATH_PATTERN}'", name="pipeline_reference"),
        CheckConstraint(
            f"char_length(requested_ref) BETWEEN 1 AND {REF_MAX_LENGTH}", name="requested_ref"
        ),
        CheckConstraint("commit_sha ~ '^[0-9a-f]{7,40}$'", name="commit_sha"),
        CheckConstraint(
            f"requested_reason IS NULL OR char_length(requested_reason) <= {REASON_MAX_LENGTH}",
            name="requested_reason",
        ),
        CheckConstraint("target_digest ~ '^[0-9a-f]{64}$'", name="target_digest"),
        CheckConstraint("poll_attempts >= 0", name="poll_attempts"),
        CheckConstraint(
            "execution_status <> 'SUCCEEDED' OR health_check_result <> '{}'::jsonb",
            name="success_evidence",
        ),
        CheckConstraint(
            "execution_status NOT IN ('RUNNING', 'SUCCEEDED') OR external_execution_id IS NOT NULL",
            name="build_number",
        ),
        CheckConstraint(
            f"execution_status NOT IN ({_in(TERMINAL)}) OR completed_at IS NOT NULL",
            name="terminal_completed",
        ),
        CheckConstraint(
            "(execution_status = 'FAILED') = (failure_reason IS NOT NULL)", name="failure_reason"
        ),
        CheckConstraint(
            "(kind = 'ROLLBACK') = (rollback_of_deployment_id IS NOT NULL)", name="rollback_pair"
        ),
        UniqueConstraint("requested_by", "idempotency_key", name="uq_deployments_idempotency"),
        Index(
            "uq_deployments_single_inflight",
            "application_id",
            "environment",
            unique=True,
            postgresql_where=text(f"execution_status IN ({_in(IN_FLIGHT)})"),
        ),
        Index("ix_deployments_application_created", "application_id", text("created_at DESC")),
        Index("ix_deployments_environment_status", "environment", "execution_status"),
        Index(
            "ix_deployments_poll",
            "next_poll_at",
            postgresql_where=text(f"execution_status IN ({_in(POLLED)})"),
        ),
        Index("ix_deployments_requested_by", "requested_by"),
        Index("ix_deployments_correlation_id", "correlation_id"),
    )
