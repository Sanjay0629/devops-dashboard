"""Declarative base and the shared naming convention.

The naming convention matters more than it looks: without it, Alembic
autogenerates anonymous constraint names that differ between the model and the
database, and a later migration cannot reliably drop or alter them
(docs/DATABASE.md §16).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        """Identity only.

        Deliberately does not render column values: a model repr ends up in
        tracebacks and log lines, and some of these tables hold references that
        should not be echoed there (docs/SECURITY.md §17).
        """
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """Time-ordered UUID primary key, generated application-side.

    Application-side generation means the id exists before the INSERT, so it can
    be used as a correlation value and returned without a round trip.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class TimestampMixin:
    """``created_at`` / ``updated_at``, both maintained by the database.

    ``updated_at`` is driven by a trigger (see the initial migration) rather
    than by application code, because "every write path remembers to set it" is
    not a property any codebase keeps for long.
    """

    # text("now()"), not the bare string: a plain string renders as a literal
    # that never compares equal to the reflected DefaultClause, so
    # `compare_server_default` reported drift on every single timestamp column.
    # A drift check that always fails is a drift check everyone learns to
    # ignore.
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class SoftDeleteMixin:
    """``deleted_at`` for the tables where a hard delete would orphan history.

    Applied only where justified (docs/DATABASE.md §10) — never to operations,
    deployments, approvals, audit events or alert events, which *are* the
    operational record.

    Every unique index on a soft-deletable table must be partial on
    ``deleted_at IS NULL``, or a name can never be reused after deletion.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def all_models() -> list[type[Any]]:
    """Import every model module so ``Base.metadata`` is complete.

    Alembic's autogenerate and the migration tests both need the full metadata;
    importing the models lazily here keeps that in one place instead of relying
    on import side effects elsewhere.
    """
    import app.approvals.models
    import app.audit.models
    import app.auth.models
    import app.authz.models
    import app.inventory.models  # noqa: F401

    return [mapper.class_ for mapper in Base.registry.mappers]
