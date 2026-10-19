"""§26.7 (M4.1). §26.2 - §26.6 arrive with the first real adapter (M4.3), beside it.

``admin:integrations`` is a no-target permission, Administrator-only like channel
administration. A read, so no audit row.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.providers import service
from app.providers.schemas import AccountCapabilities

router = APIRouter(tags=["cloud-accounts"])

Db = Annotated[AsyncSession, Depends(get_session)]
ManageIntegrations = Annotated[Authorized, require_permission("admin", "integrations")]


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
