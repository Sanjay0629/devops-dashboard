"""Alert lifecycle: ``alert_events`` (monthly partitions) and ``alert_open_keys`` (M3.3).

* ``alert_events`` is partitioned by ``first_seen_at`` like ``audit_events``, with the
  current month and the runway created here and extended by the maintenance task.
* ``alert_open_keys`` is the arbiter of "one open alert per ``dedup_key``" (``OPEN-42``):
  a partitioned table cannot enforce it, because every unique index on one must contain
  the partition key.
* ``DCP_SYSTEM`` is in the ``source`` CHECK (``OPEN-40``) for DCP's own self-monitoring.
* ``updated_at`` is maintained by the same trigger function as the other timestamped
  tables. A row trigger on a partitioned table is cloned to every partition, including
  ones created later.

Hand-written, and read against autogenerate: it cannot see partitioning, so the table
carries ``postgresql_partition_by`` and the partitions are created explicitly.

Revision ID: 0008
Revises: 0007
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("rule_name", sa.String(length=64), nullable=True),
        sa.Column("target_type", sa.String(length=16), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("environment", sa.String(length=16), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurrence_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source IN ('PROMETHEUS', 'DCP_HEALTH_CHECK', 'DCP_OPERATION', 'DCP_DEPLOYMENT', "
            "'DCP_SYSTEM', 'MANUAL')",
            name=op.f("ck_alert_events_source"),
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')", name=op.f("ck_alert_events_severity")
        ),
        sa.CheckConstraint(
            "status IN ('FIRING', 'ACKNOWLEDGED', 'RESOLVED', 'EXPIRED')",
            name=op.f("ck_alert_events_status"),
        ),
        sa.CheckConstraint(
            "target_type IN ('server', 'service', 'application', 'deployment', 'operation')",
            name=op.f("ck_alert_events_target_type"),
        ),
        sa.CheckConstraint(
            "environment IN ('development', 'staging', 'production', 'other')",
            name=op.f("ck_alert_events_environment"),
        ),
        sa.CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)", name=op.f("ck_alert_events_target_pair")
        ),
        sa.CheckConstraint("occurrence_count >= 1", name=op.f("ck_alert_events_occurrence_count")),
        sa.CheckConstraint(
            "status <> 'ACKNOWLEDGED' OR acknowledged_by IS NOT NULL",
            name=op.f("ck_alert_events_acknowledged_by"),
        ),
        sa.CheckConstraint(
            "(status = 'RESOLVED') = (resolved_at IS NOT NULL)",
            name=op.f("ck_alert_events_resolved_at"),
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"],
            ["users.id"],
            name=op.f("fk_alert_events_acknowledged_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_alert_events_assigned_to_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", "first_seen_at", name=op.f("pk_alert_events")),
        postgresql_partition_by="RANGE (first_seen_at)",
    )
    op.create_index(
        "ix_alert_events_status_severity_last_seen_at",
        "alert_events",
        ["status", "severity", sa.literal_column("last_seen_at DESC")],
        unique=False,
    )
    op.create_index("ix_alert_events_target", "alert_events", ["target_type", "target_id"])
    op.create_index("ix_alert_events_environment_status", "alert_events", ["environment", "status"])
    op.create_index("ix_alert_events_dedup_key", "alert_events", ["dedup_key"])

    op.create_table(
        "alert_open_keys",
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("first_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("dedup_key", name=op.f("pk_alert_open_keys")),
    )

    from datetime import UTC, datetime

    from app.db.partitions import DEFAULT_RUNWAY_MONTHS, create_partition_sql, months_from

    for month in months_from(datetime.now(UTC).date(), DEFAULT_RUNWAY_MONTHS + 1):
        op.execute(create_partition_sql(month, "alert_events"))

    op.execute(
        """
        CREATE TRIGGER trg_alert_events_updated_at
        BEFORE UPDATE ON alert_events
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_alert_events_updated_at ON alert_events")
    op.drop_table("alert_open_keys")
    # The partitions go with the parent.
    op.drop_table("alert_events")
