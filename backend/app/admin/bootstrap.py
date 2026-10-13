"""One-shot: grant the first ADMINISTRATOR (PROGRESS.md issue 37).

    python -m app.admin.bootstrap --email operator@example.invalid

Just-in-time provisioning deliberately creates users with no roles and there is
no seeded administrator, so a fresh deployment has nobody who can grant
anything. This command is the way out of that, and it is a **command rather
than a code path**: the rejected alternative was an auto-grant keyed on a
configured subject at first login, which works with zero operator steps but
puts a privilege-granting branch inside the authentication path — the last
place to want one.

Three properties make it safe to ship:

* **It refuses if any administrator already exists.** So it cannot be used to
  mint a second one, or to re-grant after a deliberate revocation.
* **It only grants to a user who has already signed in.** It creates nobody,
  so it cannot be used to invent an identity; the OIDC subject is whatever the
  IdP actually asserted.
* **It writes an audit event**, attributed to the named system actor
  ``system:bootstrap``. The operator is whoever holds shell access on the
  deployment, which is not an application identity — claiming it was the
  granted user would be a false attribution, and ``user_roles.granted_by`` is
  ``NOT NULL`` so the schema forces a self-reference there instead (recorded in
  the audit metadata so it is not mistaken for a real delegation).

The grant is deliberately full-scope, production included and named
explicitly, because the matcher never lets ``"*"`` imply production
(docs/SECURITY.md §4 rule 4) and a first administrator who cannot administer
production is not a first administrator.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ENVIRONMENTS
from app.audit import service as audit
from app.auth.models import User
from app.authz.models import Role, UserRole
from app.core.logging import configure_logging, get_logger
from app.db.session import get_session_factory

logger = get_logger(__name__)

ADMINISTRATOR: Final = "ADMINISTRATOR"

#: Every environment named literally, plus wildcards for the dimensions that
#: reference rows which do not exist yet on a fresh deployment.
FULL_SCOPE: Final[dict[str, list[str]]] = {
    "environment": sorted(ENVIRONMENTS),
    "cloud_account": ["*"],
    "server": ["*"],
    "application": ["*"],
}

EXIT_OK: Final = 0
EXIT_NO_SUCH_USER: Final = 1
EXIT_ALREADY_BOOTSTRAPPED: Final = 2


class BootstrapRefusedError(Exception):
    """Carries the exit code to report."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


async def bootstrap(db: AsyncSession, email: str) -> uuid.UUID:
    """Grant ADMINISTRATOR to the active user with this address."""
    user = (
        await db.execute(
            select(User).where(func.lower(User.email) == email.lower(), User.status == "ACTIVE")
        )
    ).scalar_one_or_none()
    if user is None:
        raise BootstrapRefusedError(
            f"No active user with address {email}. They must sign in once first.",
            EXIT_NO_SUCH_USER,
        )

    now = datetime.now(UTC)
    existing = (
        await db.execute(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                Role.name == ADMINISTRATOR,
                User.status == "ACTIVE",
                or_(UserRole.expires_at.is_(None), UserRole.expires_at > now),
            )
        )
    ).scalar_one()
    if existing:
        raise BootstrapRefusedError(
            "An administrator already exists. Use the admin API to grant further roles.",
            EXIT_ALREADY_BOOTSTRAPPED,
        )

    role_id = (
        await db.execute(select(Role.id).where(Role.name == ADMINISTRATOR))
    ).scalar_one_or_none()
    if role_id is None:
        raise BootstrapRefusedError(
            "The ADMINISTRATOR role is missing; run the migrations first.",
            EXIT_ALREADY_BOOTSTRAPPED,
        )

    grant = UserRole(
        user_id=user.id,
        role_id=role_id,
        scope_definition=FULL_SCOPE,
        # Self-reference, because granted_by is NOT NULL and references users:
        # the real initiator holds shell access and has no user row. The audit
        # event below is where the attribution actually lives.
        granted_by=user.id,
        expires_at=None,
    )
    db.add(grant)
    await db.flush()

    audit.record(
        db,
        action_type="ROLE_GRANTED",
        outcome="SUCCESS",
        actor_type="SYSTEM",
        actor_label="system:bootstrap",
        target_type="user_role",
        target_id=grant.id,
        metadata={
            "user_id": str(user.id),
            "role_name": ADMINISTRATOR,
            "scope_before": None,
            "scope_after": FULL_SCOPE,
            "bootstrap": True,
            "granted_by_is_self_reference": True,
        },
    )
    await db.commit()
    return grant.id


async def _run(email: str) -> int:
    factory = get_session_factory()
    async with factory() as db:
        try:
            grant_id = await bootstrap(db, email)
        except BootstrapRefusedError as refusal:
            await db.rollback()
            logger.error("bootstrap_refused", reason=str(refusal))
            return refusal.code
    logger.info("bootstrap_granted", grant_id=str(grant_id), email=email)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin.bootstrap",
        description="Grant ADMINISTRATOR to an existing user. Refuses if one already exists.",
    )
    parser.add_argument("--email", required=True, help="Address of a user who has signed in")
    arguments = parser.parse_args(argv)

    configure_logging(level="INFO", json_output=False)
    # psycopg's async driver refuses ProactorEventLoop, which is what asyncio
    # picks by default on Windows (PROGRESS.md issue 22). A Runner takes a loop
    # *factory*, which is the part uvicorn ignores and this does not; None is
    # the documented default everywhere else.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(_run(arguments.email))


if __name__ == "__main__":
    raise SystemExit(main())
