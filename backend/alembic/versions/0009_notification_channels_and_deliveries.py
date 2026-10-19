"""Notifications: ``notification_channels`` and ``notification_deliveries`` (M3.4).

* ``notification_channels.destination_reference`` is a reference (``env://NAME``) or, for
  EMAIL, an address -- never a webhook URL (``OPEN-13``, ``OPEN-48``).
* ``notification_deliveries`` is the transactional outbox (``OPEN-50``): ``UNIQUE
  (alert_event_id, channel_id)`` is what makes "one delivery per alert per channel" a
  database fact, and two CHECKs tie ``sent_at`` to ``SENT`` and ``next_attempt_at`` to
  ``PENDING`` so a terminal row cannot look as if it is waiting for a retry.
* ``alert_event_id`` carries no foreign key: it points into a partitioned table
  (DATABASE.md §15).
* ``updated_at`` is maintained by the shared ``set_updated_at()`` trigger function.

Hand-written, and read against autogenerate, which cannot see the partial index.

Revision ID: 0009
Revises: 0008
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("channel_type", sa.String(length=16), nullable=False),
        sa.Column("destination_reference", sa.String(length=512), nullable=False),
        sa.Column(
            "routing_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'ACTIVE'"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "channel_type IN ('EMAIL', 'TEAMS', 'SLACK', 'WEBHOOK')",
            name=op.f("ck_notification_channels_channel_type"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_notification_channels_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_channels")),
        sa.UniqueConstraint("name", name=op.f("uq_notification_channels_name")),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_event_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("next_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'ABANDONED')",
            name=op.f("ck_notification_deliveries_status"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 20", name=op.f("ck_notification_deliveries_attempt_count")
        ),
        sa.CheckConstraint(
            "(status = 'SENT') = (sent_at IS NOT NULL)",
            name=op.f("ck_notification_deliveries_sent_at"),
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (next_attempt_at IS NOT NULL)",
            name=op.f("ck_notification_deliveries_next_attempt_at"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channels.id"],
            name=op.f("fk_notification_deliveries_channel_id_notification_channels"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint(
            "alert_event_id",
            "channel_id",
            name=op.f("uq_notification_deliveries_alert_event_id_channel_id"),
        ),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_notification_deliveries_channel_created",
        "notification_deliveries",
        ["channel_id", sa.literal_column("created_at DESC")],
    )
    op.create_index("ix_notification_deliveries_alert", "notification_deliveries", ["alert_event_id"])
    op.create_index(
        "ix_notification_deliveries_created_at", "notification_deliveries", ["created_at"]
    )

    for table in ("notification_channels", "notification_deliveries"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )


def downgrade() -> None:
    for table in ("notification_deliveries", "notification_channels"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_channels")
