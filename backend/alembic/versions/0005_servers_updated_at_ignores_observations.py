"""``servers.updated_at`` stops moving when only an observation changed.

M2.3 makes two columns of ``servers`` a *system's* to write: ``agent_status`` and
``last_seen_at``, set by the agent probe every 30 s per monitored host. The
``updated_at`` trigger from migration 0001 fires on every UPDATE of every row,
so without this revision a probe would bump ``updated_at`` continuously.

That would break a contract rather than merely churn a column.
docs/API_CONTRACTS.md §14.4 makes ``PATCH /servers/{id}`` conditional on
``If-Unmodified-Since``, and the edit form sends the ``updated_at`` it loaded.
With the probe writing, an administrator opening the form and saving thirty
seconds later would be refused with ``409`` because *Prometheus had been
scraped*, not because anyone else edited anything. The token has to move when a
person's data moves and stand still when an observation does.

The replacement trigger compares the row with everything except the three
columns named below and only then advances ``updated_at``. It is written as a
``to_jsonb`` comparison rather than a list of columns so that a column added
later is *included* in the comparison by default: forgetting to update this
function makes ``updated_at`` move too often (a spurious ``409``, loud), not too
rarely (a lost update, silent).

``status`` is deliberately **not** excluded even though M2.5's sweep will also
write it: an administrator edits ``status`` too (PENDING ↔ MANAGED), and the
trigger cannot tell who wrote it. M2.5 owns that decision.

The downgrade restores the generic trigger exactly as 0001 created it.

Revision ID: 0005
Revises: 0004
Created: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Columns whose change alone does not count as the row having been modified.
OBSERVED = ("agent_status", "last_seen_at")


def upgrade() -> None:
    excluded = "".join(f" - '{column}'" for column in ("updated_at", *OBSERVED))
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION set_servers_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            IF (to_jsonb(NEW){excluded}) IS DISTINCT FROM (to_jsonb(OLD){excluded}) THEN
                NEW.updated_at = now();
            ELSE
                NEW.updated_at = OLD.updated_at;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER trg_servers_updated_at ON servers")
    op.execute(
        """
        CREATE TRIGGER trg_servers_updated_at
        BEFORE UPDATE ON servers
        FOR EACH ROW EXECUTE FUNCTION set_servers_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_servers_updated_at ON servers")
    op.execute(
        """
        CREATE TRIGGER trg_servers_updated_at
        BEFORE UPDATE ON servers
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )
    op.execute("DROP FUNCTION set_servers_updated_at()")
