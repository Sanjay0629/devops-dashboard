"""Open-alert counts for the dashboard and the server page, with the honesty rules attached.

Two ways a ``0`` here could lie, and what is done about each (PRD §3: never healthy merely
because nothing was reported):

* **The caller cannot read alerts** (or not for this server). Not ``0``: the section says
  it is unavailable and why, so a person without ``alert:read`` is not shown "no alerts".
* **The alerting pipeline is unconfirmed.** Counts are facts about stored alerts and are
  shown, but ``pipeline`` says whether a watchdog has recently proved that new ones would
  arrive. ``0`` next to ``not_confirmed`` reads as "we cannot tell", not "all clear".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import liveness, queries
from app.alerts.status import WATCHDOG_STALE_SECONDS
from app.authz.matcher import Grant, permits
from app.core.config import get_settings

Pipeline = Literal["confirmed", "not_confirmed", "unknown"]


async def pipeline_state(now: datetime | None = None) -> Pipeline | None:
    """Has a watchdog arrived recently? ``None`` when no Alertmanager is configured."""
    if not get_settings().alertmanager_configured:
        return None
    facts = await liveness.read()
    if facts is None:
        return "unknown"
    seen = facts["last_watchdog_at"]
    if not isinstance(seen, datetime):
        return "not_confirmed"
    age = ((now or datetime.now(UTC)) - seen).total_seconds()
    return "confirmed" if age <= WATCHDOG_STALE_SECONDS else "not_confirmed"


def can_read(grants: Sequence[Grant]) -> bool:
    return permits(grants, "alert", "read").allowed


async def open_counts(
    db: AsyncSession, grants: Sequence[Grant], server_conditions: Sequence[ColumnElement[bool]]
) -> dict[str, int]:
    """Open alerts by severity (``CRITICAL``, ``WARNING``, ``INFO``) over what is readable."""
    rows: Any = (await db.execute(queries.open_counts_query(grants, server_conditions))).all()
    counts = {severity: int(count) for severity, count in rows}
    return {name: counts.get(name, 0) for name in ("CRITICAL", "WARNING", "INFO")}
