"""Log permissions take the contract names (OPEN-32, PROGRESS issue 39, log half).

docs/SECURITY.md §4 and docs/API_CONTRACTS.md §16.7 and §19 name
``logs:read:application`` and ``logs:read:system``. The M1.2 seed created
``logs:read`` and ``logs:query``; migration 0004 reconciled the integration half
of the drift and left this half for "the milestone that first enforces the
distinction". That is M3.2.

* ``(logs, read)``  becomes ``(logs, read:application)``
* ``(logs, query)`` becomes ``(logs, read:system)``

An ``UPDATE`` rather than a drop-and-insert, so the ``role_permissions`` rows
follow the permission id and no role gains or loses anything. Keyed on the old
name, so re-running on an already-renamed catalogue is a no-op (0002's and
0004's property). ``action`` is ``varchar(32)``; the longer name is 16 characters.

The downgrade restores the old names and descriptions exactly.

Revision ID: 0007
Revises: 0006
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (old action, new action, old description, new description)
RENAMES: tuple[tuple[str, str, str, str], ...] = (
    (
        "read",
        "read:application",
        "Query application logs.",
        "Query application-source logs on servers in scope.",
    ),
    (
        "query",
        "read:system",
        "Query system and journald logs.",
        "Query system-source logs (journald, syslog, Nginx, PHP-FPM, Docker) on servers in scope.",
    ),
)

_UPDATE = sa.text(
    """
    UPDATE permissions
       SET action = :new_action, description = :description
     WHERE resource_type = 'logs' AND action = :old_action
    """
)


def upgrade() -> None:
    bind = op.get_bind()
    for old, new, _old_description, new_description in RENAMES:
        bind.execute(
            _UPDATE, {"old_action": old, "new_action": new, "description": new_description}
        )


def downgrade() -> None:
    bind = op.get_bind()
    for old, new, old_description, _new_description in RENAMES:
        bind.execute(
            _UPDATE, {"old_action": new, "new_action": old, "description": old_description}
        )
