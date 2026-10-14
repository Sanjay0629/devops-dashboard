"""Inventory idempotency columns and the ``admin:integrations`` rename.

Three changes, one revision, because they are what M1.5 owes the schema and
splitting them would mean three rebuilds of the backend image for one
milestone.

1. **``servers.idempotency_key`` / ``servers.created_by``** and the same pair
   on **``applications``**, each with a partial unique index on the pair.
   docs/API_CONTRACTS.md §7 requires an ``Idempotency-Key`` on ``POST
   /servers`` and ``POST /applications``, with a replay returning the original
   resource and ``idempotent_replay: true`` — but docs/DATABASE.md §14 stored
   keys only for ``operations`` and ``deployments``, and ``servers`` had
   ``registered_at`` and no requester column at all. Decided 2026-10-14
   (PROGRESS.md §3): the key lives on the row, exactly as §14 already does it,
   so retention is the row's lifetime (which satisfies §7's "at least 24
   hours") and a replay is a lookup on the table being written. Rejected: a
   shared ``idempotency_keys`` table, which puts idempotency in a second place
   and needs a sweep of its own.

   Both columns are **nullable** and the index is **partial** on both being
   present: rows predate this revision, and a seeded or system-created row has
   no requesting user. Nullable columns in a unique index compare as distinct
   in PostgreSQL, which would make the index silently permissive — hence the
   explicit ``WHERE`` rather than relying on that.

2. **``integration:manage`` is renamed ``admin:integrations``** (PROGRESS.md
   issue 39). docs/API_CONTRACTS.md §18, §24, §26 and docs/SECURITY.md §4 all
   name it ``admin:integrations``; the M1.2 seed created
   ``integration:manage``. §18.2 is the first endpoint to require it and
   arrives in this milestone, and no user holds a grant through it today, so
   the rename is free now and gets dearer with every endpoint that references
   it. An ``UPDATE`` rather than a drop-and-insert, so the existing
   ``role_permissions`` row for ADMINISTRATOR follows the permission id instead
   of having to be recreated.

   The log permissions (``logs:read``/``logs:query`` vs
   ``logs:read:application``/``logs:read:system``) are deliberately left alone
   until M3.2, the milestone that first enforces the distinction.

No data is destroyed and the downgrade restores both the columns' absence and
the old permission name.

Revision ID: 0004
Revises: 0003
Created: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The two tables getting the pair. Identical treatment, so a loop rather than
#: two copies that can drift.
TABLES: tuple[str, ...] = ("servers", "applications")

OLD_PERMISSION = ("integration", "manage")
NEW_PERMISSION = ("admin", "integrations")
NEW_DESCRIPTION = "Register and edit applications, cloud accounts and integration settings."
#: Exactly what migration 0002 seeded, so a downgrade restores the catalogue
#: rather than approximating it.
OLD_DESCRIPTION = "Manage cloud accounts and integrations."


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("created_by", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_created_by_users"),
            table,
            "users",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
        # Hand-written: autogenerate emits neither the partial predicate nor
        # the pairing, and without the predicate the index would not constrain
        # anything (NULLs compare as distinct).
        op.create_index(
            f"uq_{table}_created_by_idempotency_key",
            table,
            ["created_by", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("created_by IS NOT NULL AND idempotency_key IS NOT NULL"),
        )

    # Keyed on the old name, so re-running the revision on an already-renamed
    # catalogue is a no-op rather than an error (0002/0003's property).
    op.get_bind().execute(
        sa.text(
            """
            UPDATE permissions
               SET resource_type = :new_resource_type,
                   action = :new_action,
                   description = :description
             WHERE resource_type = :old_resource_type
               AND action = :old_action
            """
        ),
        {
            "old_resource_type": OLD_PERMISSION[0],
            "old_action": OLD_PERMISSION[1],
            "new_resource_type": NEW_PERMISSION[0],
            "new_action": NEW_PERMISSION[1],
            "description": NEW_DESCRIPTION,
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE permissions
               SET resource_type = :old_resource_type,
                   action = :old_action,
                   description = :description
             WHERE resource_type = :new_resource_type
               AND action = :new_action
            """
        ),
        {
            "old_resource_type": OLD_PERMISSION[0],
            "old_action": OLD_PERMISSION[1],
            "new_resource_type": NEW_PERMISSION[0],
            "new_action": NEW_PERMISSION[1],
            "description": OLD_DESCRIPTION,
        },
    )
    for table in TABLES:
        op.drop_index(f"uq_{table}_created_by_idempotency_key", table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_created_by_users"), table, type_="foreignkey")
        op.drop_column(table, "created_by")
        op.drop_column(table, "idempotency_key")
