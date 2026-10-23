"""``deployment_targets`` (M5.1; docs/DATABASE.md §5.1, ``OPEN-18``, ``OPEN-129``).

Hand-written and read against autogenerate, which cannot see partial indexes or which
constraints carry meaning. One table, and nothing else: no permission is added (``deployment:*``
and ``admin:integrations`` exist) and no row is seeded -- a fabricated registration would read as
an operational one. The ``deployments`` table, and its foreign key to this one, arrive with M5.2.

What the schema refuses whichever code wrote the row:

* ``ck_deployment_targets_pipeline_reference`` / ``..._rollback_pipeline_reference`` -- the job-path
  grammar, so a direct ``UPDATE`` cannot widen what DCP will turn into a URL path;
* ``ck_deployment_targets_rollback_pair`` -- ``rollback_supported`` is true exactly when a rollback
  pipeline is named (DCP never invents a rollback);
* ``ck_deployment_targets_parameter_template`` and ``..._allowed_ref_patterns`` -- a JSON object,
  at most ten patterns;
* ``uq_deployment_targets_application_id_environment`` -- one live target per application and
  environment, in storage rather than in a check that races (``OPEN-18``).

``updated_at`` is maintained by the shared ``set_updated_at()`` trigger. Downgrade drops the
table; it **refuses** while any target is registered and not retired, because that is
configuration somebody wrote and dropping it silently would discard it.

Revision ID: 0014
Revises: 0013
Created: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors app.deployments.paths.JOB_PATH_PATTERN. Copied, not imported: a migration must
# keep meaning what it meant when it was written.
_JOB_PATH = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){0,4}$"


def upgrade() -> None:
    op.create_table(
        "deployment_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("pipeline_reference", sa.String(length=512), nullable=False),
        sa.Column(
            "parameter_template",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "allowed_ref_patterns",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("approval_policy_id", sa.Uuid(), nullable=True),
        sa.Column(
            "health_check_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "rollback_supported", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("rollback_pipeline_reference", sa.String(length=512), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'ACTIVE'"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
            name=op.f("ck_deployment_targets_environment"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_deployment_targets_status")
        ),
        sa.CheckConstraint(
            f"pipeline_reference ~ '{_JOB_PATH}'",
            name=op.f("ck_deployment_targets_pipeline_reference"),
        ),
        sa.CheckConstraint(
            f"rollback_pipeline_reference IS NULL OR rollback_pipeline_reference ~ '{_JOB_PATH}'",
            name=op.f("ck_deployment_targets_rollback_pipeline_reference"),
        ),
        sa.CheckConstraint(
            "rollback_supported = (rollback_pipeline_reference IS NOT NULL)",
            name=op.f("ck_deployment_targets_rollback_pair"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parameter_template) = 'object'",
            name=op.f("ck_deployment_targets_parameter_template"),
        ),
        sa.CheckConstraint(
            "cardinality(allowed_ref_patterns) <= 10",
            name=op.f("ck_deployment_targets_allowed_ref_patterns"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_deployment_targets_application_id_applications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_policy_id"],
            ["approval_policies.id"],
            name=op.f("fk_deployment_targets_approval_policy_id_approval_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_deployment_targets_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_targets")),
    )
    op.create_index(
        "uq_deployment_targets_application_id_environment",
        "deployment_targets",
        ["application_id", "environment"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_deployment_targets_created_by_idempotency_key",
        "deployment_targets",
        ["created_by", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("created_by IS NOT NULL AND idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_deployment_targets_environment", "deployment_targets", ["environment"])
    op.execute(
        """
        CREATE TRIGGER trg_deployment_targets_updated_at
        BEFORE UPDATE ON deployment_targets
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    live = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM deployment_targets WHERE deleted_at IS NULL"))
        .scalar_one()
    )
    if live:
        raise RuntimeError(
            f"{live} deployment target(s) are registered and not retired: configuration somebody "
            "wrote. Retire them, or export them, before downgrading."
        )
    op.execute("DROP TRIGGER IF EXISTS trg_deployment_targets_updated_at ON deployment_targets")
    op.drop_index("ix_deployment_targets_environment", table_name="deployment_targets")
    op.drop_index(
        "uq_deployment_targets_created_by_idempotency_key", table_name="deployment_targets"
    )
    op.drop_index(
        "uq_deployment_targets_application_id_environment", table_name="deployment_targets"
    )
    op.drop_table("deployment_targets")
