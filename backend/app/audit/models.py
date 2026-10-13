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

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
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
