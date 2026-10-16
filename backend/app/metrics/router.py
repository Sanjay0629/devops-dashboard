"""Metric values (§15.1–§15.3), the catalogue (§15.4) and integration status (§26.1).

Every route is a read and none accepts PromQL (AD-04): a client names template
ids and the backend renders the query. §15.3 is a ``POST`` only because it
carries a list; it changes nothing, writes no audit row, and is allowed in
read-only mode so a contained portal can still be looked at.

Both declare the read-class rate budget and reject any query parameter, which
on a parameterless endpoint means rejecting *every* query parameter: a client
sending `?metrics=cpu_utilisation` to §15.4 has misread the contract, and
answering it with the full catalogue as though the filter applied is the
failure docs/API_CONTRACTS.md §6 is written against.

**Prometheus is never reachable from the browser.** It is published to
loopback for local development only, the SPA has no code path to it, and the
rendered PromQL does not appear in any response these routes return.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import ReadBudget
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.db.session import get_session
from app.metrics import readings, service
from app.metrics.schemas import (
    CompareRequest,
    CurrentMetrics,
    IntegrationStatusList,
    MetricComparison,
    MetricTemplateList,
    RangeMetrics,
)

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_session)]
ReadMetrics = Annotated[Authorized, require_permission("metrics", "read")]
#: §15.3 is a POST that reads. See the module docstring.
CompareMetrics = Annotated[
    Authorized, require_permission("metrics", "read", allow_in_read_only=True)
]
ReadIntegrations = Annotated[Authorized, require_permission("admin", "integrations")]

#: Empty on purpose: see the module docstring.
NO_QUERY_PARAMS = frozenset[str]()
CURRENT_QUERY_PARAMS = frozenset({"metrics"})
RANGE_QUERY_PARAMS = frozenset({"metrics", "from", "to", "step_seconds"})


@router.get(
    "/servers/{server_id}/metrics",
    tags=["metrics"],
    dependencies=[ReadBudget],
    summary="Current metric values for a server",
)
async def current_metrics(
    request: Request,
    server_id: uuid.UUID,
    db: Db,
    authorized: ReadMetrics,
    metrics: Annotated[list[str] | None, Query(max_length=32)] = None,
) -> CurrentMetrics:
    """§15.1. A value, or the reason there is none — never a zero."""
    reject_unknown_query_parameters(request, CURRENT_QUERY_PARAMS)
    chosen = readings.template_ids_or_error(metrics)
    server = await readings.metrics_server(db, server_id, authorized)
    return await readings.current(server, chosen)


@router.get(
    "/servers/{server_id}/metrics/range",
    tags=["metrics"],
    dependencies=[ReadBudget],
    summary="Metric history for a server",
)
async def range_metrics(
    request: Request,
    server_id: uuid.UUID,
    db: Db,
    authorized: ReadMetrics,
    metrics: Annotated[list[str], Query(min_length=1, max_length=32)],
    from_: Annotated[datetime, Query(alias="from")],
    to: datetime,
    step_seconds: int,
) -> RangeMetrics:
    """§15.2. Gaps are ``null`` points at their own timestamp."""
    reject_unknown_query_parameters(request, RANGE_QUERY_PARAMS)
    chosen = readings.template_ids_or_error(metrics)
    server = await readings.metrics_server(db, server_id, authorized)
    return await readings.history(server, chosen, start=from_, end=to, step_seconds=step_seconds)


@router.post(
    "/metrics/compare",
    tags=["metrics"],
    dependencies=[ReadBudget],
    summary="Compare one metric across servers or environments",
)
async def compare_metrics(
    body: CompareRequest, db: Db, authorized: CompareMetrics
) -> MetricComparison:
    """§15.3. Out-of-scope servers are dropped and counted, never erroring."""
    return await readings.compare(db, authorized, body)


@router.get(
    "/metrics/templates",
    tags=["metrics"],
    dependencies=[ReadBudget],
    summary="Available metric templates",
)
async def metric_templates(request: Request, authorized: ReadMetrics) -> MetricTemplateList:
    """§15.4. The allowlisted catalogue, with the OS families each supports."""
    reject_unknown_query_parameters(request, NO_QUERY_PARAMS)
    return service.catalogue()


@router.get(
    "/integrations/status",
    tags=["integrations"],
    dependencies=[ReadBudget],
    summary="Health of every integration",
)
async def integrations_status(
    request: Request, authorized: ReadIntegrations
) -> IntegrationStatusList:
    """§26.1. Prometheus is probed live; the rest report their real state."""
    reject_unknown_query_parameters(request, NO_QUERY_PARAMS)
    return await service.integration_status()
