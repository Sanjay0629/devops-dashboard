"""§26.7: what the adapter for a cloud account can do."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.models import CloudAccount
from app.inventory.service import NotFoundError
from app.providers.capabilities import CapabilitySet
from app.providers.errors import NotConfigured
from app.providers.registry import resolve_adapter
from app.providers.schemas import (
    AccountCapabilities,
    AccountStatus,
    ActionSupport,
    AdapterState,
    CapabilitiesOut,
)
from app.providers.types import ServiceAction


def _out(capabilities: CapabilitySet) -> CapabilitiesOut:
    return CapabilitiesOut(
        inventory_sync=capabilities.inventory_sync,
        connectivity_check=capabilities.connectivity_check,
        actions=ActionSupport(**{a.value: capabilities.actions[a] for a in ServiceAction}),
        async_handle=capabilities.async_handle,
        concurrency=capabilities.concurrency,
        latency=capabilities.latency,
    )


async def account_capabilities(db: AsyncSession, account_id: uuid.UUID) -> AccountCapabilities:
    account = (
        await db.execute(
            select(CloudAccount).where(
                CloudAccount.id == account_id, CloudAccount.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError
    status = cast(AccountStatus, account.status)
    try:
        adapter = resolve_adapter(account.provider)
    except NotConfigured as error:
        return AccountCapabilities(
            account_id=account.id,
            provider=account.provider,
            account_status=status,
            adapter_state="not_configured",
            adapter_id=None,
            capabilities=None,
            data_state="not_configured",
            reason=error.detail_code,
            demo_mode=False,
        )
    state: AdapterState = "demonstration" if adapter.demo else "configured"
    return AccountCapabilities(
        account_id=account.id,
        provider=account.provider,
        account_status=status,
        adapter_state=state,
        adapter_id=adapter.adapter_id,
        capabilities=_out(adapter.capabilities()),
        data_state="ok",
        reason=None,
        demo_mode=adapter.demo,
    )
