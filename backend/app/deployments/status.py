"""§26.1's Jenkins row (``OPEN-127``): a live probe, or the honest reason there is not one.

Never raises: the page answers "is the portal broken, or is the thing it monitors broken?", so a
Jenkins that is down is a row that says so. The reason is a closed code, never Jenkins's words, a
URL or a credential. The probe is an authenticated ``GET /api/json?tree=mode`` through the same
checked-address path and the same circuit breaker as every other Jenkins call.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.deployments.jenkins import JenkinsClient, JenkinsError, breaker
from app.metrics.schemas import IntegrationStatus

NAME = "Jenkins"


async def jenkins_row(client: JenkinsClient | None = None) -> IntegrationStatus:
    settings = get_settings()
    circuit = breaker() if client is None else client.circuit
    if settings.jenkins_url is None:
        return IntegrationStatus(
            name=NAME,
            type="ci",
            status="not_configured",
            last_error_message="DCP_JENKINS_URL is not set.",
            demo_mode=settings.demo_mode,
        )
    reason: str | None = None
    try:
        # The client is synchronous (its callers are Celery tasks); this is the one API-path use.
        await asyncio.to_thread((client or JenkinsClient(settings)).probe)
    except JenkinsError as error:
        reason = error.reason
    return IntegrationStatus(
        name=NAME,
        type="ci",
        status="ok" if reason is None else "unavailable",
        last_success_at=circuit.last_success_at,
        last_error_at=circuit.last_error_at,
        last_error_message=reason or circuit.last_error_reason,
        circuit_state=circuit.state(),
        demo_mode=settings.demo_mode,
    )
