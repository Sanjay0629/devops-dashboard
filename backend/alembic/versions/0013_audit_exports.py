"""``audit_exports`` (M4.6; docs/DATABASE.md §8.1, ``OPEN-112``).

Hand-written and read against autogenerate, which cannot see partial indexes or which
constraints carry meaning. One table, and nothing else: no permission is added (``audit:read`` and
``admin:integrations`` already exist) and ``audit_events`` is untouched.

Where a bounded export of the audit trail waits to be downloaded once. **It is not part of the
audit record and is mutable by design** -- it holds a file, not an event. Three schema facts carry
the security meaning:

* ``ck_audit_exports_content_ready`` -- the file exists exactly while ``status = 'READY'``, so
  spending, expiring or failing an export cannot leave the bytes behind;
* ``uq_audit_exports_one_active_per_requester`` -- one ``QUEUED`` / ``RUNNING`` export per
  requester, in storage rather than in a check that races;
* ``ck_audit_exports_window`` and the row / byte caps -- the 31-day, 100 000-row and 64 MiB budgets
  hold whichever code wrote the row.

Downgrade drops the table. It **refuses** while any export is ``READY``: that is a file somebody
asked for and has not downloaded, and dropping it silently would discard it.

Revision ID: 0013
Revises: 0012
Created: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("from_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("to_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("content", postgresql.BYTEA(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("retrieved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("format IN ('jsonl')", name=op.f("ck_audit_exports_format")),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'READY', 'FAILED', 'RETRIEVED', 'EXPIRED')",
            name=op.f("ck_audit_exports_status"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('export_too_large', 'requester_not_authorised', 'internal_error')",
            name=op.f("ck_audit_exports_failure_code"),
        ),
        sa.CheckConstraint(
            "(status = 'FAILED') = (failure_code IS NOT NULL)",
            name=op.f("ck_audit_exports_failed_has_code"),
        ),
        sa.CheckConstraint(
            "from_at < to_at AND to_at - from_at <= interval '31 days'",
            name=op.f("ck_audit_exports_window"),
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 64",
            name=op.f("ck_audit_exports_idempotency_key"),
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count BETWEEN 0 AND 100000",
            name=op.f("ck_audit_exports_row_count"),
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes BETWEEN 0 AND 67108864",
            name=op.f("ck_audit_exports_size_bytes"),
        ),
        sa.CheckConstraint(
            "(status = 'READY') = (content IS NOT NULL)",
            name=op.f("ck_audit_exports_content_ready"),
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (sha256 IS NOT NULL AND row_count IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            name=op.f("ck_audit_exports_ready_is_described"),
        ),
        sa.CheckConstraint(
            "(status = 'RETRIEVED') = (retrieved_at IS NOT NULL)",
            name=op.f("ck_audit_exports_retrieved_consistent"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('READY', 'FAILED', 'RETRIEVED') OR completed_at IS NOT NULL",
            name=op.f("ck_audit_exports_finished_completed"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_audit_exports_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_exports")),
        sa.UniqueConstraint(
            "requested_by", "idempotency_key", name=op.f("uq_audit_exports_idempotency")
        ),
    )
    op.create_index(
        "uq_audit_exports_one_active_per_requester",
        "audit_exports",
        ["requested_by"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )
    op.create_index(
        "ix_audit_exports_expiry",
        "audit_exports",
        ["expires_at"],
        postgresql_where=sa.text("status = 'READY'"),
    )


def downgrade() -> None:
    ready = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM audit_exports WHERE status = 'READY'"))
        .scalar_one()
    )
    if ready:
        raise RuntimeError(
            f"{ready} audit export(s) are READY: files somebody asked for and has not downloaded. "
            "Download or let them expire before downgrading."
        )
    op.drop_index("ix_audit_exports_expiry", table_name="audit_exports")
    op.drop_index("uq_audit_exports_one_active_per_requester", table_name="audit_exports")
    op.drop_table("audit_exports")
