"""§26.1's Loki row, from a live probe (M3.2).

Never raises: the point of the integration page is to answer "is the portal broken or
is the thing it reads broken" (PRD §4.11), and an endpoint that fails when its
dependency fails cannot. The row carries a reason *code* from a closed set, never the
URL or Loki's own words (docs/SECURITY.md §20).
"""

from __future__ import annotations

from app.core.config import get_settings
from app.logs.client import LokiClient, LokiUnavailableError, breaker
from app.metrics.schemas import IntegrationStatus


async def loki_status(client: LokiClient | None = None) -> IntegrationStatus:
    settings = get_settings()
    circuit = breaker()
    if not settings.loki_configured:
        return IntegrationStatus(
            name="Loki",
            type="logs",
            status="not_configured",
            last_error_message="DCP_LOKI_URL is not set.",
            demo_mode=settings.demo_mode,
        )
    client = client or LokiClient(settings=settings)
    reason: str | None = None
    try:
        await client.probe()
    except LokiUnavailableError as error:
        reason = error.reason
    return IntegrationStatus(
        name="Loki",
        type="logs",
        status="ok" if reason is None else "unavailable",
        last_success_at=circuit.last_success_at,
        last_error_at=circuit.last_error_at,
        last_error_message=reason or circuit.last_error_reason,
        circuit_state=circuit.state(),
        demo_mode=settings.demo_mode,
    )
