"""Alert lifecycle tables (docs/DATABASE.md §9, ``OPEN-41``, ``OPEN-42``).

**``alert_events``** is partitioned monthly by ``first_seen_at`` so the 180-day retention is
a partition drop, never a mass delete. PostgreSQL requires the partition key in every
unique index on a partitioned table, which is exactly why the "one open row per
``dedup_key``" rule **cannot live here**: ``UNIQUE (dedup_key, first_seen_at)`` is satisfied
by two open rows that began at different moments.

**``alert_open_keys``** is where that rule lives (``OPEN-42``): an ordinary table whose
primary key is the ``dedup_key``, inserted ``ON CONFLICT DO NOTHING`` by ingestion in the
same transaction as the event, and deleted by whatever closes the event. The invariant is a
database constraint, not a lock convention.

Two schema facts carry the PRD §4.8 acceptance criterion that *acknowledgement does not
resolve*: ``resolved_at`` is non-null **exactly when** the status is ``RESOLVED``, and
``ACKNOWLEDGED`` needs an acknowledger. An acknowledged alert therefore cannot carry a
resolution time, whatever the application does.

``target_type``/``target_id`` are polymorphic and carry no foreign key (docs/DATABASE.md
§15); they are NULL together for an alert whose labels matched nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.alerts.lifecycle import SEVERITIES, SOURCES, STATUSES
from app.approvals.models import ENVIRONMENTS
from app.db.base import Base
from app.db.types import enum_check, uuid7

ALERT_TARGET_TYPES: Final[tuple[str, ...]] = (
    "server",
    "service",
    "application",
    "deployment",
    "operation",
)

SUMMARY_MAX_LENGTH: Final = 1_000


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Partition key, and therefore part of the primary key. DCP's clock, never the
    # payload's ``startsAt``: an untrusted timestamp could name a month with no partition.
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        primary_key=True,
        nullable=False,
        server_default=text("now()"),
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(String(SUMMARY_MAX_LENGTH), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurrence_count: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        enum_check("source", SOURCES, "source"),
        enum_check("severity", SEVERITIES, "severity"),
        enum_check("status", STATUSES, "status"),
        enum_check("target_type", ALERT_TARGET_TYPES, "target_type"),
        enum_check("environment", ENVIRONMENTS, "environment"),
        CheckConstraint("(target_type IS NULL) = (target_id IS NULL)", name="target_pair"),
        CheckConstraint("occurrence_count >= 1", name="occurrence_count"),
        CheckConstraint(
            "status <> 'ACKNOWLEDGED' OR acknowledged_by IS NOT NULL", name="acknowledged_by"
        ),
        # Acknowledgement never implies resolution (PRD §4.8), as a schema fact.
        CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name="resolved_at"),
        Index(
            "ix_alert_events_status_severity_last_seen_at",
            "status",
            "severity",
            text("last_seen_at DESC"),
        ),
        Index("ix_alert_events_target", "target_type", "target_id"),
        Index("ix_alert_events_environment_status", "environment", "status"),
        Index("ix_alert_events_dedup_key", "dedup_key"),
        {"postgresql_partition_by": "RANGE (first_seen_at)"},
    )


class AlertOpenKey(Base):
    """One row per open alert, keyed by ``dedup_key``: the arbiter of ``OPEN-42``."""

    __tablename__ = "alert_open_keys"

    dedup_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The open event this key points at. ``first_seen_at`` is carried so the lookup is a
    #: primary-key hit on one partition rather than a scan of all of them.
    alert_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
