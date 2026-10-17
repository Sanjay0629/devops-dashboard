"""``servers.updated_at`` stops moving when the sweep flips ``status``.

Migration 0005 left ``status`` inside the ``updated_at`` comparison on purpose:
"an administrator edits ``status`` too, and the trigger cannot tell who wrote it.
M2.5 owns that decision." This is that decision.

The M2.5 sweep moves a scraped server between ``MANAGED``, ``STALE`` and
``UNREACHABLE`` as it goes quiet and comes back. ``PATCH /servers/{id}`` is
conditional on ``If-Unmodified-Since`` and the edit form sends the ``updated_at``
it loaded, so a sweep flip that moved the token would make an administrator's
save answer ``409`` because a host went quiet, not because anyone else edited
anything — the failure 0005 exists to prevent for ``agent_status``.

**What the rule is.** ``status`` is left out of the comparison *only when both
the old and the new value are one of the three the sweep owns.* Any other change
still moves ``updated_at``:

* ``PENDING`` → ``MANAGED`` (an administrator accepting a registration),
* anything → ``RETIRED``, and ``PENDING``/``RETIRED`` back out of it,
* any change that also edits another column, even alongside a sweep-style flip.

The trigger cannot tell who wrote the row, so an administrator's ``STALE`` →
``MANAGED`` (permitted by the API) does not move the token either. That is the
price of not asking who wrote it: the sweep would revert the value on its next
pass if the host were still quiet, and an edit to any *other* field in the same
request still moves it.

Like 0005 this is a ``to_jsonb`` comparison rather than a column list, so a
column added later is compared by default (a spurious ``409``, loud) instead of
ignored (a lost update, silent).

The downgrade restores 0005's function exactly.

Revision ID: 0006
Revises: 0005
Created: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OBSERVED = ("agent_status", "last_seen_at")
SWEPT = ("MANAGED", "STALE", "UNREACHABLE")


def _function(*, with_sweep_rule: bool) -> str:
    base = "".join(f" - '{column}'" for column in ("updated_at", *OBSERVED))
    if not with_sweep_rule:
        body = f"""
            IF (to_jsonb(NEW){base}) IS DISTINCT FROM (to_jsonb(OLD){base}) THEN
                NEW.updated_at = now();
            ELSE
                NEW.updated_at = OLD.updated_at;
            END IF;
            RETURN NEW;"""
    else:
        swept = ", ".join(f"'{state}'" for state in SWEPT)
        body = f"""
            IF OLD.status IN ({swept}) AND NEW.status IN ({swept}) THEN
                -- Both ends are states the sweep owns: not a person's edit.
                IF (to_jsonb(NEW){base} - 'status') IS DISTINCT FROM
                   (to_jsonb(OLD){base} - 'status') THEN
                    NEW.updated_at = now();
                ELSE
                    NEW.updated_at = OLD.updated_at;
                END IF;
            ELSIF (to_jsonb(NEW){base}) IS DISTINCT FROM (to_jsonb(OLD){base}) THEN
                NEW.updated_at = now();
            ELSE
                NEW.updated_at = OLD.updated_at;
            END IF;
            RETURN NEW;"""
    return f"""
        CREATE OR REPLACE FUNCTION set_servers_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN{body}
        END;
        $$ LANGUAGE plpgsql
        """


def upgrade() -> None:
    op.execute(_function(with_sweep_rule=True))


def downgrade() -> None:
    op.execute(_function(with_sweep_rule=False))
