"""``deployment_targets`` (docs/DATABASE.md §5.1; ``OPEN-18``, ``OPEN-118`` – ``OPEN-129``).

A target is a **registered pipeline**: the job path DCP may run for one application in one
environment, the parameters it may send, the refs it may deploy and the approval policy that
gates it. Jenkins itself (base URL, user, token reference) is deployment configuration, not a
column: one Jenkins per deployment, and nothing here can name another.

The database enforces what an application bug must not be able to widen: the job-path grammar,
that the rollback pair travels together, the pattern count, and one live target per application
and environment.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.approvals.models import ENVIRONMENTS
from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check
from app.deployments.paths import JOB_PATH_PATTERN
from app.deployments.refs import MAX_PATTERNS

TARGET_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "DISABLED")


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
    )
