"""Metrics catalogue (§15.4) and integration status (§26.1).

Two reads, two different permissions, and nothing here accepts PromQL — §15.4
takes no parameters at all (AD-04).

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

from typing import Annotated

from fastapi import APIRouter, Request

from app.auth.dependencies import ReadBudget
from app.authz.dependencies import Authorized, require_permission
from app.core.filters import reject_unknown_query_parameters
from app.metrics import service
from app.metrics.schemas import IntegrationStatusList, MetricTemplateList

router = APIRouter()

ReadMetrics = Annotated[Authorized, require_permission("metrics", "read")]
ReadIntegrations = Annotated[Authorized, require_permission("admin", "integrations")]

#: Empty on purpose: see the module docstring.
NO_QUERY_PARAMS = frozenset[str]()


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
