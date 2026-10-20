"""``approvals`` (M4.5; docs/DATABASE.md §7, ``OPEN-94``), ``EXPIRED``, and ``admin:policies``.

Hand-written and read against autogenerate, which cannot see partial indexes or which constraints
carry meaning. Three things happen here:

1. **``approvals``** -- one row per request, created ``PENDING`` and decided exactly once.
   ``ck_approvals_decided_consistent`` ties ``decision = PENDING`` to ``decided_at IS NULL``;
   ``ck_approvals_decider_present`` makes a decided, non-expired row name its decider (the worker
   reads that as evidence, ``OPEN-99``); ``uq_approvals_one_open_per_operation`` makes "one open
   request per operation" a storage guarantee; ``uq_approvals_one_decision_per_approver`` is
   DATABASE.md §7's index; the other three indexes serve the queue and the expiry sweep. No
   foreign key on ``operation_id`` (DATABASE.md §15: it will point at deployments too).
2. **``operations.approval_status`` gains ``EXPIRED``** -- a request nobody decided, or one a cancel
   withdrew. The CHECK is dropped and recreated.
3. **``admin:policies``** -- the permission behind the approval-policy editor (``OPEN-105``),
   granted to ``ADMINISTRATOR`` only (docs/SECURITY.md §4). Idempotent, like 0002 and 0003.

Downgrade drops the table and the permission. It **refuses** if any operation is ``EXPIRED``:
mapping that to an older value would falsify what happened to a request, so a person decides.

Revision ID: 0012
Revises: 0011
Created: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED'"
NEW_STATUSES = "'NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED', 'EXPIRED'"

PERMISSION = ("admin", "policies", "Create, edit and delete approval policies.")


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("approval_policy_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column(
            "decision", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation_type IN ('SERVICE_OPERATION', 'DEPLOYMENT')",
            name=op.f("ck_approvals_operation_type"),
        ),
        sa.CheckConstraint(
            "decision IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name=op.f("ck_approvals_decision"),
        ),
        sa.CheckConstraint(
            "(decision = 'PENDING') = (decided_at IS NULL)",
            name=op.f("ck_approvals_decided_consistent"),
        ),
        sa.CheckConstraint(
            "decision IN ('PENDING', 'EXPIRED') OR approved_by IS NOT NULL",
            name=op.f("ck_approvals_decider_present"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_policy_id"],
            ["approval_policies.id"],
            name=op.f("fk_approvals_approval_policy_id_approval_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_approvals_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name=op.f("fk_approvals_approved_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(
        "uq_approvals_one_decision_per_approver",
        "approvals",
        ["operation_type", "operation_id", "approved_by"],
        unique=True,
        postgresql_where=sa.text("approved_by IS NOT NULL"),
    )
    op.create_index(
        "uq_approvals_one_open_per_operation",
        "approvals",
        ["operation_type", "operation_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'PENDING'"),
    )
    op.create_index("ix_approvals_operation", "approvals", ["operation_type", "operation_id"])
    op.create_index(
        "ix_approvals_pending",
        "approvals",
        ["decision"],
        postgresql_where=sa.text("decision = 'PENDING'"),
    )
    op.create_index(
        "ix_approvals_expiry",
        "approvals",
        ["expires_at"],
        postgresql_where=sa.text("decision = 'PENDING'"),
    )

    op.drop_constraint(op.f("ck_operations_approval_status"), "operations", type_="check")
    op.create_check_constraint(
        op.f("ck_operations_approval_status"), "operations", f"approval_status IN ({NEW_STATUSES})"
    )

    connection = op.get_bind()
    resource_type, action, description = PERMISSION
    connection.execute(
        sa.text(
            """
            INSERT INTO permissions (id, resource_type, action, scope_definition,
                                     description, created_at)
            VALUES (gen_random_uuid(), :resource_type, :action, '{}'::jsonb,
                    :description, now())
            ON CONFLICT (resource_type, action, scope_definition) DO NOTHING
            """
        ),
        {"resource_type": resource_type, "action": action, "description": description},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id, effect, created_at)
            SELECT r.id, p.id, 'ALLOW', now()
              FROM roles r
              JOIN permissions p ON p.resource_type = :resource_type AND p.action = :action
             WHERE r.name = 'ADMINISTRATOR'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        {"resource_type": resource_type, "action": action},
    )


def downgrade() -> None:
    connection = op.get_bind()
    expired = connection.execute(
        sa.text("SELECT count(*) FROM operations WHERE approval_status = 'EXPIRED'")
    ).scalar_one()
    if expired:
        raise RuntimeError(
            f"{expired} operation(s) have approval_status = 'EXPIRED'; there is no older value "
            "that says the same thing. Decide what they become before downgrading."
        )
    resource_type, action, _description = PERMISSION
    connection.execute(
        sa.text(
            """
            DELETE FROM role_permissions rp
             USING permissions p
             WHERE rp.permission_id = p.id
               AND p.resource_type = :resource_type AND p.action = :action
            """
        ),
        {"resource_type": resource_type, "action": action},
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE resource_type = :resource_type AND action = :action"
        ),
        {"resource_type": resource_type, "action": action},
    )

    op.drop_constraint(op.f("ck_operations_approval_status"), "operations", type_="check")
    op.create_check_constraint(
        op.f("ck_operations_approval_status"), "operations", f"approval_status IN ({OLD_STATUSES})"
    )

    op.drop_index("ix_approvals_expiry", table_name="approvals")
    op.drop_index("ix_approvals_pending", table_name="approvals")
    op.drop_index("ix_approvals_operation", table_name="approvals")
    op.drop_index("uq_approvals_one_open_per_operation", table_name="approvals")
    op.drop_index("uq_approvals_one_decision_per_approver", table_name="approvals")
    op.drop_table("approvals")
