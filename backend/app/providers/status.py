"""§26.1's per-account rows (``OPEN-87``).

Decided from what is *known*, with no AWS call on a status-page poll:

* the adapter cannot run (demonstration mode, a static key, no document version)
  -> ``not_configured`` with that reason;
* account ``DISABLED``                                -> ``not_configured`` / ``account_disabled``;
* never verified (or the cache is gone)               -> ``degraded`` / ``not_verified_yet``
  (unproven is not ``ok``: the M3.4 channel precedent);
* last verification had every probed permission       -> ``ok``;
* last verification did not                           -> ``unavailable`` with its closed reason.

Returns ``None`` when there is no real-provider account, so the caller keeps the single
``Cloud accounts`` row it has always had. Never raises.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.inventory.models import CloudAccount
from app.metrics.schemas import IntegrationStatus
from app.providers import accounts, registry
from app.providers.errors import NotConfigured


async def account_rows(db: AsyncSession) -> list[IntegrationStatus] | None:
    demo = get_settings().demo_mode
    try:
        rows = (
            (
                await db.execute(
                    select(CloudAccount)
                    .where(
                        CloudAccount.deleted_at.is_(None),
                        CloudAccount.provider.in_(sorted(registry.registered_providers())),
                    )
                    .order_by(CloudAccount.display_name, CloudAccount.id)
                )
            )
            .scalars()
            .all()
        )
        verified = await accounts.cached_verifications([a.id for a in rows])
    except SQLAlchemyError:
        return [
            IntegrationStatus(
                name="Cloud accounts",
                type="cloud",
                status="unavailable",
                last_error_message="database_unreadable",
                demo_mode=demo,
            )
        ]
    if not rows:
        return None
    out: list[IntegrationStatus] = []
    for account in rows:
        name = f"Cloud account: {account.display_name}"
        try:
            blocker = (
                "demonstration_mode" if registry.resolve_adapter(account.provider).demo else None
            )
        except NotConfigured as error:
            blocker = error.detail_code or "not_configured"
        known = verified.get(account.id)
        if blocker is not None:
            state, reason = "not_configured", blocker
        elif account.status != "ACTIVE":
            state, reason = "not_configured", "account_disabled"
        elif known is None:
            state, reason = "degraded", "not_verified_yet"
        elif known.permissions_ok:
            state, reason = "ok", None
        else:
            state, reason = "unavailable", known.message
        out.append(
            IntegrationStatus(
                name=name,
                type="cloud",
                status=state,  # type: ignore[arg-type]
                last_success_at=known.checked_at if state == "ok" and known else None,
                last_error_at=known.checked_at if state == "unavailable" and known else None,
                last_error_message=reason,
                demo_mode=demo,
            )
        )
    return out
