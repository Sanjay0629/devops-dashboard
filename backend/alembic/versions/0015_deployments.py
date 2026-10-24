"""``deployments`` (M5.2; docs/DATABASE.md §5.2, ``OPEN-135``).

Hand-written and read against autogenerate, which cannot see partial indexes or the ``DESC``
ordering and would not know which constraints carry meaning. What the schema refuses whichever code
wrote the row:

* ``uq_deployments_idempotency`` -- the same requester and key is one deployment (PRD §4.10);
* ``uq_deployments_single_inflight`` -- a partial unique index, so "one active deployment per
  application and environment" is a storage guarantee rather than a check that races;
* ``ck_deployments_success_evidence`` -- a ``SUCCEEDED`` row has a non-empty health result (AD-10,
  rule 8; the state machine refuses it too and each guard is tested alone);
* ``ck_deployments_build_number`` -- a ``RUNNING`` or ``SUCCEEDED`` row has its build number;
* ``ck_deployments_terminal_completed`` -- a terminal row has a completion time;
* ``ck_deployments_failure_reason`` -- a row is ``FAILED`` exactly when it has a failure reason;
* ``ck_deployments_rollback_pair`` -- ``kind = ROLLBACK`` exactly when it names the deployment it
  rolls back; the job-path grammar, the ref length, the commit-SHA shape and the digest shape.

``execution_status`` has exactly the PRD's nine values. ``ON DELETE RESTRICT`` throughout: a
deployment is the record of a privileged action and is **never deleted**, so downgrade refuses
while any exists (the record would be discarded silently). ``updated_at`` is maintained by the
shared ``set_updated_at()`` trigger. No permission is added and no row is seeded.

``fk_deployments_deployment_target_id_deployment_targets`` is **composite** on ``(target,
application, environment)`` and ``uq_deployment_targets_id_application_id_environment`` is added to
``deployment_targets`` for it to point at: a deployment cannot name a target of another
application, nor store an environment that is not its target's.

Beyond DATABASE §5.2 as it stood: ``requested_reason`` (the contract's ``reason``, which the table
had no column for), ``failure_reason``, ``queue_item_id``, ``target_digest``, ``next_poll_at``,
``poll_attempts``, ``last_polled_at``, and the ``build_number``, ``terminal_completed``,
``failure_reason``, ``requested_ref``, ``requested_reason`` and ``pipeline_reference`` checks.

Revision ID: 0015
Revises: 0014
Created: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied, not imported: a migration must keep meaning what it meant when it was written.
_JOB_PATH = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){0,4}$"
_IN_FLIGHT = "'APPROVED', 'AWAITING_APPROVAL', 'QUEUED', 'REQUESTED', 'RUNNING'"
_TERMINAL = "'CANCELLED', 'FAILED', 'ROLLED_BACK', 'SUCCEEDED'"


def upgrade() -> None:
    # The key the composite foreign key below points at: it is what makes a deployment's
    # application and environment *the target's*, so the stored environment -- the authorisation
    # basis for reading it -- cannot disagree with the pipeline it ran.
    op.create_unique_constraint(
        "uq_deployment_targets_id_application_id_environment",
        "deployment_targets",
        ["id", "application_id", "environment"],
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_target_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("pipeline_reference", sa.String(length=512), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("requested_ref", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'DEPLOY'"), nullable=False),
        sa.Column("rollback_of_deployment_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_reason", sa.Text(), nullable=True),
        sa.Column("approval_status", sa.String(length=16), nullable=False),
        sa.Column("execution_status", sa.String(length=24), nullable=False),
        sa.Column("external_execution_id", sa.String(length=64), nullable=True),
        sa.Column("queue_item_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("target_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "health_check_result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sanitized_result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("last_poll_error", sa.Text(), nullable=True),
        sa.Column("last_polled_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_poll_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("poll_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deadline_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
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
            name=op.f("ck_deployments_environment"),
        ),
        sa.CheckConstraint(
            "execution_status IN ('REQUESTED', 'AWAITING_APPROVAL', 'APPROVED', 'QUEUED', "
            "'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'ROLLED_BACK')",
            name=op.f("ck_deployments_execution_status"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED')",
            name=op.f("ck_deployments_approval_status"),
        ),
        sa.CheckConstraint("kind IN ('DEPLOY', 'ROLLBACK')", name=op.f("ck_deployments_kind")),
        sa.CheckConstraint(
            f"pipeline_reference ~ '{_JOB_PATH}'", name=op.f("ck_deployments_pipeline_reference")
        ),
        sa.CheckConstraint(
            "char_length(requested_ref) BETWEEN 1 AND 200",
            name=op.f("ck_deployments_requested_ref"),
        ),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{7,40}$'", name=op.f("ck_deployments_commit_sha")
        ),
        sa.CheckConstraint(
            "requested_reason IS NULL OR char_length(requested_reason) <= 500",
            name=op.f("ck_deployments_requested_reason"),
        ),
        sa.CheckConstraint(
            "target_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_deployments_target_digest")
        ),
        sa.CheckConstraint("poll_attempts >= 0", name=op.f("ck_deployments_poll_attempts")),
        sa.CheckConstraint(
            "execution_status <> 'SUCCEEDED' OR health_check_result <> '{}'::jsonb",
            name=op.f("ck_deployments_success_evidence"),
        ),
        sa.CheckConstraint(
            "execution_status NOT IN ('RUNNING', 'SUCCEEDED') OR external_execution_id IS NOT NULL",
            name=op.f("ck_deployments_build_number"),
        ),
        sa.CheckConstraint(
            f"execution_status NOT IN ({_TERMINAL}) OR completed_at IS NOT NULL",
            name=op.f("ck_deployments_terminal_completed"),
        ),
        sa.CheckConstraint(
            "(execution_status = 'FAILED') = (failure_reason IS NOT NULL)",
            name=op.f("ck_deployments_failure_reason"),
        ),
        sa.CheckConstraint(
            "(kind = 'ROLLBACK') = (rollback_of_deployment_id IS NOT NULL)",
            name=op.f("ck_deployments_rollback_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_deployments_application_id_applications"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_target_id", "application_id", "environment"],
            [
                "deployment_targets.id",
                "deployment_targets.application_id",
                "deployment_targets.environment",
            ],
            name=op.f("fk_deployments_deployment_target_id_deployment_targets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_deployments_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_deployment_id"],
            ["deployments.id"],
            name=op.f("fk_deployments_rollback_of_deployment_id_deployments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
        sa.UniqueConstraint("requested_by", "idempotency_key", name="uq_deployments_idempotency"),
    )
    op.create_index(
        "uq_deployments_single_inflight",
        "deployments",
        ["application_id", "environment"],
        unique=True,
        postgresql_where=sa.text(f"execution_status IN ({_IN_FLIGHT})"),
    )
    op.create_index(
        "ix_deployments_application_created",
        "deployments",
        ["application_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_deployments_environment_status", "deployments", ["environment", "execution_status"]
    )
    op.create_index(
        "ix_deployments_poll",
        "deployments",
        ["next_poll_at"],
        postgresql_where=sa.text("execution_status IN ('QUEUED', 'RUNNING')"),
    )
    op.create_index("ix_deployments_requested_by", "deployments", ["requested_by"])
    op.create_index("ix_deployments_correlation_id", "deployments", ["correlation_id"])
    op.execute(
        """
        CREATE TRIGGER trg_deployments_updated_at
        BEFORE UPDATE ON deployments
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    existing = op.get_bind().execute(sa.text("SELECT count(*) FROM deployments")).scalar_one()
    if existing:
        raise RuntimeError(
            f"{existing} deployment(s) exist: they are the audit record of what was deployed and "
            "are never deleted. Export them before downgrading, and drop the table by hand."
        )
    op.execute("DROP TRIGGER IF EXISTS trg_deployments_updated_at ON deployments")
    op.drop_index("ix_deployments_correlation_id", table_name="deployments")
    op.drop_index("ix_deployments_requested_by", table_name="deployments")
    op.drop_index("ix_deployments_poll", table_name="deployments")
    op.drop_index("ix_deployments_environment_status", table_name="deployments")
    op.drop_index("ix_deployments_application_created", table_name="deployments")
    op.drop_index("uq_deployments_single_inflight", table_name="deployments")
    op.drop_table("deployments")
    op.drop_constraint(
        "uq_deployment_targets_id_application_id_environment",
        "deployment_targets",
        type_="unique",
    )
