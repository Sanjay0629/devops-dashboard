"""Append-only audit trail (docs/DATABASE.md §8, AD-11, mandatory rule 10).

Three structural decisions carry the security meaning:

1. **No ``updated_at``, no ``deleted_at``.** The absence is the point: there is
   no legitimate update to an audit event, and no API that performs one.
2. **Monthly range partitions** on ``occurred_at``, so retention is an
   export-then-``DROP PARTITION`` rather than a mass delete, and so growth
   never requires a schema rewrite. PostgreSQL requires the partition key in
   the primary key, hence ``PRIMARY KEY (id, occurred_at)``.
3. **Polymorphic target with no foreign key.** An audit insert must never fail
   because of a referential constraint — losing the audit event is worse than
   losing referential integrity — and events must survive their target's
   deletion (docs/DATABASE.md §15).

The ``INSERT``/``SELECT``-only grant is applied by the migration, so
immutability travels with the schema rather than living in a runbook.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, INET, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import enum_check, uuid7

#: PRD §4.12 says "actor **or service identity**", which a single nullable
#: actor_id cannot express.
ACTOR_TYPES: Final[tuple[str, ...]] = ("USER", "SYSTEM", "INTEGRATION")
OUTCOMES: Final[tuple[str, ...]] = ("SUCCESS", "FAILURE", "DENIED")

TARGET_TYPES: Final[tuple[str, ...]] = (
    "user",
    "role",
    "user_role",
    "cloud_account",
    "server",
    "application",
    "service",
    "service_action_policy",
    "approval_policy",
    "operation",
    "deployment",
    "deployment_target",
    "approval",
    "alert_event",
    "notification_channel",
    "audit",
    "system",
)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Partition key, and therefore part of the primary key.
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        primary_key=True,
        nullable=False,
        server_default=text("now()"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL only when actor_type is not USER — enforced by a CHECK below.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # Preserved verbatim, so the trail stays readable even if the user row is
    # later renamed or disabled. e.g. "operator@example.invalid",
    # "system:scheduler", "system:alertmanager".
    actor_label: Mapped[str] = mapped_column(String(256), nullable=False)

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)

    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    # No FK: audit inserts must not depend on the approval row's lifecycle.
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ip: Mapped[ipaddress.IPv4Address | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Redacted before insert. Never a raw request body, token or secret
    # (PRD §4.12, docs/SECURITY.md §17).
    sanitized_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        enum_check("actor_type", ACTOR_TYPES, "actor_type"),
        enum_check("outcome", OUTCOMES, "outcome"),
        # Mandatory rule 10: no unattributable privileged event.
        CheckConstraint(
            "(actor_type = 'USER') = (actor_id IS NOT NULL)",
            name="ck_audit_events_actor_attribution",
        ),
        Index("ix_audit_events_occurred_at", text("occurred_at DESC")),
        Index("ix_audit_events_actor_id_occurred_at", "actor_id", "occurred_at"),
        Index("ix_audit_events_target", "target_type", "target_id", "occurred_at"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
        Index("ix_audit_events_action_type_occurred_at", "action_type", "occurred_at"),
        Index(
            "ix_audit_events_denied",
            "occurred_at",
            postgresql_where=text("outcome = 'DENIED'"),
        ),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


#: ``audit_exports.status`` (docs/DATABASE.md §8.1, ``OPEN-112``).
EXPORT_STATUSES: Final[tuple[str, ...]] = (
    "QUEUED",
    "RUNNING",
    "READY",
    "FAILED",
    "RETRIEVED",
    "EXPIRED",
)
#: Closed, so a failure says what happened without carrying provider or database text.
EXPORT_FAILURE_CODES: Final[tuple[str, ...]] = (
    "export_too_large",
    "requester_not_authorised",
    "internal_error",
)
#: Design budgets (``OPEN-112``), not measurements. The CHECKs hold them whatever the task does.
EXPORT_MAX_ROWS: Final = 100_000
EXPORT_MAX_BYTES: Final = 64 * 1024 * 1024
EXPORT_MAX_WINDOW_DAYS: Final = 31


class AuditExport(Base, UUIDPrimaryKeyMixin):
    """Where a bounded export of the audit trail waits to be downloaded once (M4.6).

    **Not part of the audit record, and mutable by design:** it holds a file, not an event. Three
    schema facts carry the security meaning and hold whatever the application does:

    * ``ck_audit_exports_content_ready`` -- the file exists exactly while the export is ``READY``,
      so spending, expiring or failing an export cannot leave the bytes behind;
    * ``uq_audit_exports_one_active_per_requester`` -- one running request per requester;
    * ``ck_audit_exports_window`` -- at most 31 days, whichever code wrote the row.

    Single retrieval is a guarded ``UPDATE ... RETURNING`` (``app/audit/exports.py``); this table
    cannot make two downloads impossible on its own, but ``content_ready`` makes the second one
    find nothing.
    """

    __tablename__ = "audit_exports"

    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    from_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    to_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The ``jsonl`` file. Present exactly while ``status = 'READY'``.
    content: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    #: ``created_at`` + 24 hours; the application sets it (a default cannot read ``created_at``).
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        enum_check("format", ("jsonl",), "format"),
        enum_check("status", EXPORT_STATUSES, "status"),
        CheckConstraint(
            "failure_code IS NULL OR failure_code IN ("
            + ", ".join(f"'{code}'" for code in EXPORT_FAILURE_CODES)
            + ")",
            name="failure_code",
        ),
        CheckConstraint("(status = 'FAILED') = (failure_code IS NOT NULL)", name="failed_has_code"),
        CheckConstraint(
            f"from_at < to_at AND to_at - from_at <= interval '{EXPORT_MAX_WINDOW_DAYS} days'",
            name="window",
        ),
        CheckConstraint("char_length(idempotency_key) BETWEEN 8 AND 64", name="idempotency_key"),
        CheckConstraint(
            f"row_count IS NULL OR row_count BETWEEN 0 AND {EXPORT_MAX_ROWS}", name="row_count"
        ),
        CheckConstraint(
            f"size_bytes IS NULL OR size_bytes BETWEEN 0 AND {EXPORT_MAX_BYTES}",
            name="size_bytes",
        ),
        CheckConstraint("(status = 'READY') = (content IS NOT NULL)", name="content_ready"),
        CheckConstraint(
            "status <> 'READY' OR (sha256 IS NOT NULL AND row_count IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            name="ready_is_described",
        ),
        CheckConstraint(
            "(status = 'RETRIEVED') = (retrieved_at IS NOT NULL)", name="retrieved_consistent"
        ),
        CheckConstraint(
            "status NOT IN ('READY', 'FAILED', 'RETRIEVED') OR completed_at IS NOT NULL",
            name="finished_completed",
        ),
        UniqueConstraint("requested_by", "idempotency_key", name="uq_audit_exports_idempotency"),
        Index(
            "uq_audit_exports_one_active_per_requester",
            "requested_by",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
        Index(
            "ix_audit_exports_expiry",
            "expires_at",
            postgresql_where=text("status = 'READY'"),
        ),
    )
