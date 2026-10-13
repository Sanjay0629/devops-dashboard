"""Identity model (docs/DATABASE.md §3.1).

There is no password column, and there never will be: authentication is entirely
external via OIDC (PRD §4.1, docs/SECURITY.md §2). A test asserts the absence,
because "we just won't add one" is not a control.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_check

USER_STATUSES: Final[tuple[str, ...]] = ("ACTIVE", "DISABLED")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    # The OIDC subject, not the email: an address can be reassigned to a
    # different person, a subject cannot.
    identity_provider_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    # Stored alongside the subject so a future IdP migration cannot collide two
    # providers that happen to issue the same subject value.
    identity_provider_issuer: Mapped[str] = mapped_column(String(512), nullable=False)

    # Display and notification routing only — never an authorisation input.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ACTIVE'"))
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "identity_provider_issuer",
            "identity_provider_subject",
            name="uq_users_identity_provider_issuer_identity_provider_subject",
        ),
        enum_check("status", USER_STATUSES, "status"),
        # Case-insensitive uniqueness among active users only, so a retired
        # address can be reused later. A functional index avoids depending on
        # the citext extension, which needs privileges the app role may not have.
        Index(
            "uq_users_email_active",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
