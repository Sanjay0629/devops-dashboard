"""§26.1's Alertmanager row (``OPEN-47``): a probe *and* proof that alerts flow.

A bare readiness probe says only that a process answers. What an operator needs is "if
something fails, will DCP hear about it", and the always-firing ``DcpWatchdog`` rule answers
that: it exists only if Prometheus evaluates rules, sends them to Alertmanager, and
Alertmanager delivers them to the webhook. So the row is ``ok`` only when **both** hold:

* Alertmanager's ``/-/ready`` answers, and
* a watchdog reached the webhook within :data:`WATCHDOG_STALE_SECONDS`.

Otherwise it says which one failed, as a reason code from a closed set -- never the URL or
the peer's own words (SECURITY.md §20). A row that said ``ok`` for a pipeline nobody had
heard from would be exactly the "healthy because nothing was reported" PRD §3 forbids.

Never raises: an endpoint that fails when its dependency fails cannot say whether the portal
or the dependency is broken.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import httpx

from app.alerts import liveness
from app.alerts.lifecycle import REPEAT_INTERVAL_SECONDS
from app.core.config import get_settings
from app.metrics.schemas import IntegrationStatus

#: Two repeat intervals: a watchdog re-notifies hourly, so one missed repeat is tolerated
#: and two are not.
WATCHDOG_STALE_SECONDS: Final = 2 * REPEAT_INTERVAL_SECONDS
_PROBE_TIMEOUT: Final = 3.0


async def _ready(transport: httpx.AsyncBaseTransport | None) -> str | None:
    """``None`` if Alertmanager reports ready, else a reason code."""
    base = get_settings().alertmanager_url
    if base is None:  # the caller checked; this keeps the type honest without an assert
        return "alertmanager_unreachable"
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=_PROBE_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.get(f"{base}/-/ready")
    except httpx.TimeoutException:
        return "alertmanager_timeout"
    except httpx.HTTPError:
        return "alertmanager_unreachable"
    return None if response.status_code == 200 else "alertmanager_not_ready"


async def alertmanager_status(
    transport: httpx.AsyncBaseTransport | None = None, *, now: datetime | None = None
) -> IntegrationStatus:
    settings = get_settings()
    if not settings.alertmanager_configured:
        return IntegrationStatus(
            name="Alertmanager",
            type="alerts",
            status="not_configured",
            last_error_message="DCP_ALERTMANAGER_URL is not set.",
            demo_mode=settings.demo_mode,
        )
    now = now or datetime.now(UTC)
    problem = await _ready(transport)
    facts = await liveness.read()
    watchdog: datetime | None = None
    if facts is None:
        problem = problem or "alert_liveness_unreadable"
    else:
        raw = facts["last_watchdog_at"]
        watchdog = raw if isinstance(raw, datetime) else None
        if problem is None:
            if watchdog is None:
                problem = "watchdog_never_seen"
            elif (now - watchdog).total_seconds() > WATCHDOG_STALE_SECONDS:
                problem = "watchdog_stale"
    return IntegrationStatus(
        name="Alertmanager",
        type="alerts",
        status="ok" if problem is None else "unavailable",
        # The last time a delivery *proved* the pipeline, not the last probe.
        last_success_at=watchdog,
        last_error_message=problem,
        demo_mode=settings.demo_mode,
    )
