"""Notification tables (docs/DATABASE.md §9.2, §9.3; ``OPEN-13``, ``OPEN-50``, ``OPEN-53``).

``notification_channels`` holds a *reference* to a destination, never a secret: for SLACK,
TEAMS and WEBHOOK it is ``env://NAME``, for EMAIL the (non-secret) address itself.

``notification_deliveries`` is the outbox: one row per (alert, channel), inserted in the same
transaction as the alert row it is about, and the per-delivery record of success, failure and
retry that PRD §4.8 makes an acceptance criterion. ``alert_event_id`` has no foreign key --
it points into a partitioned table and the project's polymorphic references carry none
(DATABASE.md §15).

Two schema facts keep the lifecycle honest, whatever the application does:

* ``sent_at`` is non-null **exactly when** the status is ``SENT``;
* ``next_attempt_at`` is non-null **exactly when** the status is ``PENDING``, so a row that
  will never be retried cannot look as if it is waiting for one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check
from app.notifications.destination import CHANNEL_STATUSES, CHANNEL_TYPES

DELIVERY_STATUSES: Final[tuple[str, ...]] = ("PENDING", "SENT", "FAILED", "ABANDONED")
RETRYABLE_STATUSES: Final[tuple[str, ...]] = ("FAILED", "ABANDONED")
NAME_MAX_LENGTH: Final = 100
REASON_MAX_LENGTH: Final = 64


class NotificationChannel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notification_channels"

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False, unique=True)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    routing_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))

    __table_args__ = (
        enum_check("channel_type", CHANNEL_TYPES, "channel_type"),
        enum_check("status", CHANNEL_STATUSES, "status"),
    )


class NotificationDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notification_deliveries"

    alert_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    #: A closed reason code (``http_404``, ``timeout``, ...), never a URL or the peer's words.
    last_error: Mapped[str | None] = mapped_column(String(REASON_MAX_LENGTH), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("alert_event_id", "channel_id"),
        enum_check("status", DELIVERY_STATUSES, "status"),
        CheckConstraint("attempt_count BETWEEN 0 AND 20", name="attempt_count"),
        CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name="sent_at"),
        CheckConstraint(
            "(status = 'PENDING') = (next_attempt_at IS NOT NULL)", name="next_attempt_at"
        ),
        Index(
            "ix_notification_deliveries_due",
            "status",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_notification_deliveries_channel_created", "channel_id", text("created_at DESC")),
        Index("ix_notification_deliveries_alert", "alert_event_id"),
        Index("ix_notification_deliveries_created_at", "created_at"),
    )
