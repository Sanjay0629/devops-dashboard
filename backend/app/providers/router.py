"""§26.2 - §26.7 (cloud accounts) and §14.8 (a server's connectivity check).

``admin:integrations`` is a no-target permission, Administrator-only like channel
administration. §14.8 is ``server:write`` against the server's own scope. Every write is
audited; §26.2 and §26.7 are reads and are not.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget, client_ip, user_agent
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.providers import accounts, connectivity, service
from app.providers.schemas import (
    AccountCapabilities,
    AccountCreate,
    AccountList,
    AccountOut,
    AccountUpdate,
    ConnectivityOut,
    SyncAccepted,
    VerificationOut,
)

router = APIRouter(tags=["cloud-accounts"])

Db = Annotated[AsyncSession, Depends(get_session)]
ManageIntegrations = Annotated[Authorized, require_permission("admin", "integrations")]
WriteServers = Annotated[Authorized, require_permission("server", "write")]


@router.get(
    "/cloud-accounts/{account_id}/capabilities",
    dependencies=[ReadBudget],
    summary="What the adapter for this cloud account can do",
)
async def account_capabilities(
    account_id: uuid.UUID, request: Request, db: Db, authorized: ManageIntegrations
) -> AccountCapabilities:
    """`200` for any existing account; `capabilities` is `null` when no adapter serves it."""
    reject_unknown_query_parameters(request, frozenset())
    return await service.account_capabilities(db, account_id)


@router.get("/cloud-accounts", dependencies=[ReadBudget], summary="List cloud accounts")
async def list_accounts(request: Request, db: Db, authorized: ManageIntegrations) -> AccountList:
    """At most 200 accounts, newest configuration first by name. No secret is ever returned."""
    reject_unknown_query_parameters(request, frozenset())
    return await accounts.list_accounts(db)


@router.post("/cloud-accounts", status_code=201, summary="Register a cloud account")
async def create_account(
    body: AccountCreate, request: Request, db: Db, authorized: ManageIntegrations
) -> AccountOut:
    """The credential is a *reference* (an IAM role ARN for AWS); a key is a ``422``."""
    return await accounts.create_account(
        db, authorized, body, client_ip=client_ip(request), user_agent=user_agent(request)
    )


@router.patch("/cloud-accounts/{account_id}", summary="Edit or disable a cloud account")
async def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    request: Request,
    db: Db,
    authorized: ManageIntegrations,
) -> AccountOut:
    """``DISABLED`` stops every operation against the account's servers at once."""
    return await accounts.update_account(
        db,
        authorized,
        account_id,
        body,
        client_ip=client_ip(request),
        user_agent=user_agent(request),
    )


@router.post(
    "/cloud-accounts/{account_id}/verify", summary="Verify an account's credentials and reads"
)
async def verify_account(
    account_id: uuid.UUID, request: Request, db: Db, authorized: ManageIntegrations
) -> VerificationOut:
    """Read-only: ``ssm:SendCommand`` is reported in ``not_probed``, never tried."""
    return await accounts.verify_account(
        db, authorized, account_id, client_ip=client_ip(request), user_agent=user_agent(request)
    )


@router.post(
    "/cloud-accounts/{account_id}/sync",
    status_code=202,
    summary="Reconcile the account's registered servers with the provider",
)
async def sync_account(
    account_id: uuid.UUID, request: Request, db: Db, authorized: ManageIntegrations
) -> SyncAccepted:
    """Never registers a server and never changes an environment (``OPEN-88``)."""
    return await accounts.request_sync(
        db, authorized, account_id, client_ip=client_ip(request), user_agent=user_agent(request)
    )


@router.post(
    "/servers/{server_id}/connectivity-check",
    summary="Can DCP reach this server through its provider?",
)
async def connectivity_check(
    server_id: uuid.UUID, request: Request, db: Db, authorized: WriteServers
) -> ConnectivityOut:
    """Changes nothing about the server; ``reachable: false`` is an answer, not an error."""
    return await connectivity.check(
        db, authorized, server_id, client_ip=client_ip(request), user_agent=user_agent(request)
    )
