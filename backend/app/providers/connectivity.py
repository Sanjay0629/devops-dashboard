"""§14.8: can DCP reach this server through its provider? (``OPEN-86``)

A synchronous probe through the adapter, in a worker thread, bounded at 10 s. **It changes nothing
about the server**: ``agent_status``, ``status`` and ``last_seen_at`` stay owned by the Prometheus
probe and the sweep (``OPEN-30``); a second writer would make the pages disagree.

``reachable = false`` (the host is not registered with the provider, or its agent is offline) is
an *answer*. A provider that cannot answer is an error: a ``503`` or ``504``, never a ``false``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.authz.dependencies import Authorized
from app.core import ratelimit
from app.inventory.models import CloudAccount
from app.inventory.service import get_server, require_write_scope
from app.providers import registry
from app.providers.capabilities import Support
from app.providers.errors import (
    NotConfigured,
    ProviderError,
    Timeout,
    UnsupportedOperation,
    to_app_error,
)
from app.providers.refs import server_ref
from app.providers.schemas import ConnectivityOut

BOUND_SECONDS = 10.0
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 60

_MESSAGE = {True: "reachable", False: "not_reachable"}


async def check(
    db: AsyncSession,
    authorized: Authorized,
    server_id: uuid.UUID,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> ConnectivityOut:
    server = await get_server(db, server_id, authorized)
    await require_write_scope(server, authorized)
    account = await db.get(CloudAccount, server.cloud_account_id)
    # Every refusal below happens before any provider call.
    if account is None or account.deleted_at is not None or account.status != "ACTIVE":
        raise to_app_error(NotConfigured(detail_code="account_disabled"))
    await ratelimit.enforce(
        "connectivity",
        str(server.id),
        limit=RATE_LIMIT,
        window_seconds=RATE_WINDOW_SECONDS,
        fail_closed=True,
    )
    outcome = None
    failure: ProviderError | None = None
    try:
        adapter = registry.resolve_adapter(account.provider)
        if adapter.demo:
            raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="demonstration_mode")
        support = adapter.capabilities().connectivity_check
        if support is Support.UNSUPPORTED:
            raise UnsupportedOperation(adapter_id=adapter.adapter_id, detail_code="connectivity")
        if support is not Support.SUPPORTED:
            raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="connectivity")
        try:
            ref = server_ref(server, account)
        except ValueError:
            raise NotConfigured(
                adapter_id=adapter.adapter_id, detail_code="malformed_reference"
            ) from None
        outcome = await asyncio.wait_for(
            asyncio.to_thread(adapter.check_connectivity, ref), timeout=BOUND_SECONDS
        )
    except TimeoutError:
        failure = Timeout(detail_code="connectivity_timed_out")
    except ProviderError as error:
        failure = error
    if outcome is not None and (
        outcome.data_state != "ok" or outcome.value is None or outcome.collected_at is None
    ):
        failure = NotConfigured(detail_code="no_answer")
    if failure is not None or outcome is None or outcome.value is None:
        failure = failure or NotConfigured(detail_code="no_answer")
        await _record(
            db,
            authorized,
            server.id,
            server.environment,
            "FAILURE",
            failure.provider_error_class,
            client_ip,
            user_agent,
        )
        raise to_app_error(failure)
    reachable = outcome.value.reachable
    await _record(
        db,
        authorized,
        server.id,
        server.environment,
        "SUCCESS",
        "reachable" if reachable else outcome.value.provider_status,
        client_ip,
        user_agent,
    )
    return ConnectivityOut(
        reachable=reachable,
        checked_at=outcome.collected_at or datetime.now(UTC),
        provider_status=outcome.value.provider_status,
        data_state="ok",
        message=_MESSAGE[reachable],
    )


async def _record(
    db: AsyncSession,
    authorized: Authorized,
    server_id: uuid.UUID,
    environment: str,
    outcome: str,
    result: str,
    client_ip: str | None,
    user_agent: str | None,
) -> None:
    audit.record(
        db,
        action_type="SERVER_CONNECTIVITY_CHECKED",
        outcome=outcome,
        actor_id=authorized.user.id,
        actor_label=authorized.user.email,
        target_type="server",
        target_id=server_id,
        environment=environment,
        client_ip=client_ip,
        user_agent=user_agent,
        metadata={"result": result},
    )
    await db.commit()
