"""``operations`` (M4.2; docs/DATABASE.md §6, §6.1).

Hand-written and read against autogenerate, which cannot see the partial indexes or the
``DESC`` ordering and would not know which constraints carry meaning:

* ``uq_operations_idempotency`` -- the same requester and key is one operation;
* ``uq_operations_single_inflight`` -- a partial unique index, so "one in flight per target" is
  a storage guarantee (it does not guard ``target_service_id IS NULL``; no such operation is
  built);
* ``ck_operations_verified_success`` -- a ``SUCCEEDED`` row carries ``observed_state_after``;
* ``ck_operations_terminal_completed`` -- a terminal row has a completion time.

``execution_status`` has exactly the PRD's nine values; ``VERIFYING`` is never stored
(``OPEN-69``). ``ON DELETE RESTRICT`` throughout: rows are the record of a privileged action
and are never deleted. ``updated_at`` is maintained by the shared ``set_updated_at()`` trigger.

Revision ID: 0010
Revises: 0009
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_server_id", sa.Uuid(), nullable=False),
        sa.Column("target_service_id", sa.Uuid(), nullable=True),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_reason", sa.Text(), nullable=True),
        sa.Column("approval_status", sa.String(length=16), nullable=False),
        sa.Column("execution_status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("external_execution_id", sa.String(length=256), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("observed_state_before", sa.String(length=16), nullable=True),
        sa.Column("observed_state_after", sa.String(length=16), nullable=True),
        sa.Column(
            "sanitized_result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("deadline_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("parent_operation_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            "environment IN ('development', 'staging', 'production', 'other')",
            name=op.f("ck_operations_environment"),
        ),
        sa.CheckConstraint(
            "action_type IN ('start', 'stop', 'restart', 'status', 'logs_tail')",
            name=op.f("ck_operations_action_type"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED')",
            name=op.f("ck_operations_approval_status"),
        ),
        sa.CheckConstraint(
            "execution_status IN ('PENDING', 'AWAITING_APPROVAL', 'APPROVED', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED', 'REJECTED')",
            name=op.f("ck_operations_execution_status"),
        ),
        sa.CheckConstraint(
            "execution_status <> 'SUCCEEDED' OR observed_state_after IS NOT NULL",
            name=op.f("ck_operations_verified_success"),
        ),
        sa.CheckConstraint(
            "execution_status NOT IN ('CANCELLED', 'FAILED', 'REJECTED', 'SUCCEEDED', 'TIMED_OUT') "
            "OR completed_at IS NOT NULL",
            name=op.f("ck_operations_terminal_completed"),
        ),
        sa.ForeignKeyConstraint(
            ["target_server_id"],
            ["servers.id"],
            name=op.f("fk_operations_target_server_id_servers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_service_id"],
            ["services.id"],
            name=op.f("fk_operations_target_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_operations_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_operation_id"],
            ["operations.id"],
            name=op.f("fk_operations_parent_operation_id_operations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operations")),
        sa.UniqueConstraint("requested_by", "idempotency_key", name="uq_operations_idempotency"),
    )
    op.create_index(
        "uq_operations_single_inflight",
        "operations",
        ["target_server_id", "target_service_id"],
        unique=True,
        postgresql_where=sa.text(
            "execution_status IN ('APPROVED', 'AWAITING_APPROVAL', 'PENDING', 'RUNNING')"
        ),
    )
    op.create_index(
        "ix_operations_server_created",
        "operations",
        ["target_server_id", sa.literal_column("created_at DESC")],
    )
    op.create_index(
        "ix_operations_waiting",
        "operations",
        ["execution_status"],
        postgresql_where=sa.text("execution_status IN ('PENDING', 'APPROVED', 'RUNNING')"),
    )
    op.create_index(
        "ix_operations_requester_created",
        "operations",
        ["requested_by", sa.literal_column("created_at DESC")],
    )
    op.create_index("ix_operations_correlation_id", "operations", ["correlation_id"])
    op.create_index(
        "ix_operations_deadline",
        "operations",
        ["deadline_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )

    op.execute(
        """
        CREATE TRIGGER trg_operations_updated_at
        BEFORE UPDATE ON operations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_operations_updated_at ON operations")
    op.drop_table("operations")
