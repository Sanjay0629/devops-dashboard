"""Liveness and readiness endpoints (docs/API_CONTRACTS.md §11).

Both are public — the only public endpoints that exist in M1.1. Neither returns
a version, hostname, connection string or error text: a readiness probe is an
unauthenticated endpoint and is treated as one.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict

from app.health.checks import is_ready, run_checks

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    checks: dict[str, bool]


@router.get("/healthz", response_model=HealthResponse, summary="Liveness")
async def healthz() -> HealthResponse:
    """Liveness only: the process is up and serving. No dependencies touched."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse, summary="Readiness")
async def readyz(response: Response) -> ReadinessResponse:
    """Readiness: can this instance serve traffic?

    Returns per-dependency booleans and nothing else. 503 when a required
    dependency is down, so an orchestrator stops routing to this instance.
    """
    checks = await run_checks()
    ready = is_ready(checks)
    response.status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if ready else "unavailable", checks=checks)
