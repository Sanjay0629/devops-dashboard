"""Synthetic local inventory: ``python -m app.inventory.seed`` (CLAUDE.md §7).

Development data, and it says so in every place someone could read it:

* every ``display_name`` starts with ``[DEMO]``;
* every hostname is under ``.invalid``, the TLD RFC 2606 reserves for exactly
  this and which can never resolve;
* every ``provider_metadata`` carries ``"synthetic": true``, so a query can
  find and remove the lot;
* ``created_by`` is left ``NULL`` — nobody registered these, and attributing
  them to a real administrator would put a false name in the inventory.

It also **refuses to run outside the ``local`` environment**. Demonstration
data in a real deployment is the failure mode CLAUDE.md §7 is written against,
and a flag someone has to remember to omit is not a control.

Idempotent: keyed on ``(cloud_account_id, provider_resource_id)`` and on
``slug``, so a second run changes nothing and a partially seeded database
completes. Removal is ``--remove``, which deletes exactly the rows this script
creates and nothing else.
"""

from __future__ import annotations

import argparse
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import all_models
from app.db.session import get_sync_engine
from app.inventory.models import Application, ApplicationServer, CloudAccount, Server

# ``servers`` and ``applications`` reference ``users`` since migration 0004, so
# the mapper needs every model registered before it can resolve those foreign
# keys. The application gets that for free from ``create_app``; a standalone
# script has to ask for it.
all_models()

logger = get_logger(__name__)

DEMO_PREFIX: Final = "[DEMO]"
DEMO_DOMAIN: Final = "demo.invalid"
SYNTHETIC: Final[dict[str, Any]] = {"synthetic": True, "source": "app.inventory.seed"}

#: (slug, name, owner_team)
APPLICATIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("demo-checkout", f"{DEMO_PREFIX} Checkout API", "payments"),
    ("demo-ledger", f"{DEMO_PREFIX} Ledger", "payments"),
    ("demo-portal", f"{DEMO_PREFIX} Customer Portal", "platform"),
)

#: (resource id, hostname label, environment, os, management method, status,
#: application slugs). One application deliberately spans two environments and
#: one server hosts two applications — PRD §4.9's acceptance criterion in both
#: directions, visible without having to construct it by hand.
SERVERS: Final[tuple[tuple[str, str, str, str, str, str, tuple[str, ...]], ...]] = (
    (
        "i-0demo00000000001",
        "web-01",
        "production",
        "LINUX",
        "AWS_SSM",
        "MANAGED",
        ("demo-checkout", "demo-portal"),
    ),
    (
        "i-0demo00000000002",
        "web-02",
        "production",
        "LINUX",
        "AWS_SSM",
        "MANAGED",
        ("demo-checkout",),
    ),
    (
        "i-0demo00000000003",
        "ledger-01",
        "production",
        "LINUX",
        "AWS_SSM",
        "STALE",
        ("demo-ledger",),
    ),
    (
        "i-0demo00000000004",
        "web-01-stg",
        "staging",
        "LINUX",
        "AWS_SSM",
        "MANAGED",
        ("demo-checkout", "demo-portal"),
    ),
    (
        "i-0demo00000000005",
        "ledger-01-stg",
        "staging",
        "LINUX",
        "AWS_SSM",
        "PENDING",
        ("demo-ledger",),
    ),
    (
        "i-0demo00000000006",
        "build-01",
        "development",
        "WINDOWS",
        "RESTRICTED_AGENT",
        "UNREACHABLE",
        (),
    ),
)


def _account(session: Session) -> CloudAccount:
    """The ``ON_PREM`` account migration 0002 seeds, or a demo one beside it.

    The seeded account is used as-is rather than created: it is the row the
    schema expects non-cloud servers to reference, and adding a second would
    make the demo data look like a separate integration.
    """
    account = (
        session.execute(
            select(CloudAccount)
            .where(CloudAccount.deleted_at.is_(None))
            .order_by(CloudAccount.created_at)
        )
        .scalars()
        .first()
    )
    if account is None:
        raise SystemExit(
            "No cloud account exists. Run 'alembic upgrade head' first — migration "
            "0002 seeds the ON_PREM account these servers reference."
        )
    return account


def seed(session: Session) -> tuple[int, int]:
    """Create the synthetic rows that are missing. Returns ``(servers, apps)``."""
    account = _account(session)

    applications: dict[str, Application] = {}
    created_applications = 0
    for slug, name, owner_team in APPLICATIONS:
        application = session.execute(
            select(Application).where(Application.slug == slug)
        ).scalar_one_or_none()
        if application is None:
            application = Application(
                name=name,
                slug=slug,
                description="Synthetic demonstration data created by app.inventory.seed.",
                owner_team=owner_team,
                status="ACTIVE",
            )
            session.add(application)
            session.flush()
            created_applications += 1
        applications[slug] = application

    created_servers = 0
    for resource_id, label, environment, operating_system, method, status, slugs in SERVERS:
        server = session.execute(
            select(Server).where(
                Server.cloud_account_id == account.id,
                Server.provider_resource_id == resource_id,
            )
        ).scalar_one_or_none()
        if server is None:
            server = Server(
                cloud_account_id=account.id,
                provider_resource_id=resource_id,
                hostname=f"{label}.{DEMO_DOMAIN}",
                display_name=f"{DEMO_PREFIX} {label}",
                operating_system=operating_system,
                os_version="synthetic",
                environment=environment,
                region="demo-region-1",
                owner_team="platform",
                management_method=method,
                monitoring_enabled=True,
                metrics_instance=f"{label}.{DEMO_DOMAIN}:9100",
                status=status,
                provider_metadata=dict(SYNTHETIC),
            )
            session.add(server)
            session.flush()
            created_servers += 1
        for slug in slugs:
            link = session.get(ApplicationServer, (applications[slug].id, server.id))
            if link is None:
                session.add(
                    ApplicationServer(application_id=applications[slug].id, server_id=server.id)
                )
    session.flush()
    return created_servers, created_applications


def remove(session: Session) -> tuple[int, int]:
    """Delete exactly the rows :func:`seed` creates."""
    server_ids = list(
        session.execute(
            select(Server.id).where(Server.provider_metadata["synthetic"].as_boolean().is_(True))
        )
        .scalars()
        .all()
    )
    application_ids = list(
        session.execute(
            select(Application.id).where(
                Application.slug.in_([slug for slug, _name, _team in APPLICATIONS])
            )
        )
        .scalars()
        .all()
    )
    if server_ids:
        session.execute(
            delete(ApplicationServer).where(ApplicationServer.server_id.in_(server_ids))
        )
        session.execute(delete(Server).where(Server.id.in_(server_ids)))
    if application_ids:
        session.execute(
            delete(ApplicationServer).where(ApplicationServer.application_id.in_(application_ids))
        )
        session.execute(delete(Application).where(Application.id.in_(application_ids)))
    return len(server_ids), len(application_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="delete the synthetic rows instead")
    arguments = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=not settings.is_local)
    if not settings.is_local:
        logger.error(
            "seed_refused",
            reason="synthetic inventory belongs in local development only (CLAUDE.md §7)",
            environment=settings.environment,
        )
        return 2

    with Session(get_sync_engine(), future=True) as session, session.begin():
        servers, applications = remove(session) if arguments.remove else seed(session)
    logger.info(
        "seed_removed" if arguments.remove else "seed_created",
        servers=servers,
        applications=applications,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
