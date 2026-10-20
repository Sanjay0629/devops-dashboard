"""``operations.heartbeat_at`` (M4.3; docs/DATABASE.md §6, ``OPEN-84``).

The worker that owns a ``RUNNING`` operation writes this on every poll. A row with a recorded
provider handle whose heartbeat has gone quiet is picked up by the healer, and exactly one
resumer wins it with a compare-and-set on the value it saw. ``NULL`` means "never polled": a row
the executor claimed but that has no handle is not resumable and is timed out by the reaper, as in
M4.2.

One nullable column and no index: the healer's scan is over the rows the existing partial index
(``execution_status IN ('PENDING','APPROVED','RUNNING')``) already narrows to a handful.

Revision ID: 0011
Revises: 0010
Created: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operations", sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("operations", "heartbeat_at")
