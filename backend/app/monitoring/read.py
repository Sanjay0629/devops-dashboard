"""Host health on the read path: the reference, the gates, and a SQL twin (M2.5).

Three things, each answering "may this host be judged, and by what?":

**The reference (D2).** Age is measured against the last time Prometheus
answered (``marker``), not the wall clock. It is *usable* only while fresh —
three probe intervals, the same bound the services cache uses. An old marker
means Prometheus has stopped answering, and judging hosts against it would
freeze them in a verdict nobody is confirming; so a stale, missing or
unconfigured reference means **no host is judged at all**: ``state`` is
``None`` (not ``unknown`` — nothing was evaluated), and ``data_state`` says why.

**The gates.** A retired server, an unsupported operating system, monitoring
switched off and a server with no ``metrics_instance`` are ``unknown`` however
old ``last_seen_at`` is: silence nobody was listening to is not news. The three
that are about monitoring come from ``services.normalise.gate`` — the same
function the service views use — so the two cannot disagree about what "not
monitored" means. A host that was never seen is ``unknown`` too.

**The SQL twin.** :func:`health_state_expr` is :func:`evaluate`'s *state* written
as a ``CASE``, for the dashboard's rollup and its ``health_status`` filter, which
have to count and page by the state a row will show. It is **a translation of
the rule, not a second decision**, and is held to it the way
``services.listing.displayed_state`` is: ``tests/integration/test_health_parity``
runs a table of rows through both and fails if they disagree. If the order here
changes, change both.

The twin compares timestamps (``last_seen_at < reference - N s``) rather than
extracting an age, so the boundary is exactly the Python one: ``age > N`` is
``last_seen_at < reference - N``. No float, no rounding.

``critical`` is never produced. It would come from utilisation (``OPEN-28``),
which this milestone does not evaluate, and ``unknown`` is the honest answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, NamedTuple

from redis.exceptions import RedisError
from sqlalchemy import ColumnElement, case, not_

from app.approvals.models import ENVIRONMENTS
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.inventory.models import Server
from app.inventory.schemas import DataState
from app.monitoring.health import HealthState, HealthThresholds, HealthThresholdTable
from app.monitoring.health import classify_health as _classify
from app.monitoring.marker import MARKER_KEY, parse_marker
from app.services.normalise import LINUX, gate

logger = get_logger(__name__)

#: How many probe intervals old the marker may be and still be a reference.
FRESH_PROBE_INTERVALS: Final = 3

_GATE_REASONS: Final = {
    "unsupported_os": "Monitoring is not supported for this operating system.",
    "monitoring_disabled": "Monitoring is disabled for this server.",
    "no_metrics_instance": "No metrics instance is registered for this server.",
}
RETIRED_REASON: Final = "This server is retired."
NEVER_SEEN_REASON: Final = "This server has not been scraped yet."
NOT_CONFIGURED_REASON: Final = "Prometheus is not configured, so health cannot be evaluated."
NO_ANSWER_REASON: Final = "Prometheus has not answered yet, so health cannot be evaluated."


@dataclass(frozen=True, slots=True)
class Reference:
    """What hosts are aged against, and whether that can be trusted."""

    data_state: Literal["ok", "stale", "unavailable", "not_configured"]
    #: The marker whatever its age, so a stale one can still be *dated*.
    at: datetime | None
    reason: str | None

    @property
    def usable(self) -> datetime | None:
        return self.at if self.data_state == "ok" else None


def classify_reference(
    *, configured: bool, at: datetime | None, now: datetime, fresh_within_seconds: int
) -> Reference:
    if not configured:
        return Reference("not_configured", None, NOT_CONFIGURED_REASON)
    if at is None:
        return Reference("unavailable", None, NO_ANSWER_REASON)
    # Strict, like every other bound here: exactly on it is still fresh.
    if (now - at).total_seconds() > fresh_within_seconds:
        return Reference(
            "stale",
            at,
            f"Prometheus last answered at {at.isoformat()}; health is not judged against old data.",
        )
    return Reference("ok", at, None)


async def current_reference(settings: Settings) -> Reference:
    """Read the marker. Redis being down is "no answer", never an error."""
    at: datetime | None = None
    if settings.prometheus_configured:
        try:
            at = parse_marker(await get_redis().get(MARKER_KEY))
        except (RedisError, OSError):
            logger.warning("prometheus_marker_unreadable")
    return classify_reference(
        configured=settings.prometheus_configured,
        at=at,
        now=datetime.now(UTC),
        fresh_within_seconds=FRESH_PROBE_INTERVALS * settings.agent_probe_interval_seconds,
    )


class ServerHealth(NamedTuple):
    #: ``None`` when no evaluation happened (the reference is unusable);
    #: ``unknown`` when one did and nothing is known about *this* host.
    state: HealthState | None
    data_state: DataState
    unavailable_reason: str | None
    collected_at: datetime | None
    stale_after_seconds: int

    @property
    def stale(self) -> bool | None:
        """Past the ``stale`` line. ``None`` when that cannot be said — never ``False``."""
        if self.state is None or self.state == "unknown":
            return None
        return self.state in ("stale", "unreachable")


def _gate(server: Server) -> tuple[DataState, str] | None:
    if server.status == "RETIRED":
        return "unavailable", RETIRED_REASON
    gated = gate(
        server.operating_system, server.monitoring_enabled, server.metrics_instance is not None
    )
    if gated is not None:
        # `gate` only ever returns a reason for the cases above.
        return gated.data_state, _GATE_REASONS[str(gated.unavailable_reason)]
    return None


def evaluate(server: Server, table: HealthThresholdTable, reference: Reference) -> ServerHealth:
    """One server's health. The SQL twin is :func:`health_state_expr`."""
    thresholds = table.for_environment(server.environment)
    stale_after = thresholds.stale_after_seconds
    usable = reference.usable
    if usable is None:
        return ServerHealth(
            None,
            reference.data_state,
            reference.reason,
            reference.at if reference.data_state == "stale" else None,
            stale_after,
        )
    gated = _gate(server)
    if gated is not None:
        return ServerHealth("unknown", gated[0], gated[1], None, stale_after)
    if server.last_seen_at is None:
        return ServerHealth("unknown", "unavailable", NEVER_SEEN_REASON, None, stale_after)
    verdict = _classify(thresholds, server.last_seen_at, usable)
    return ServerHealth(verdict.state, verdict.data_state, None, server.last_seen_at, stale_after)


# --- the SQL twin -----------------------------------------------------------------------


def _tier(thresholds: HealthThresholds, reference: datetime) -> ColumnElement[str]:
    """The time ladder for one set of lines. ``age > N`` is ``seen < reference - N``."""

    def before(seconds: int) -> ColumnElement[bool]:
        return Server.last_seen_at < reference - timedelta(seconds=seconds)

    return case(
        (Server.last_seen_at.is_(None), "unknown"),
        (before(thresholds.unreachable_after_seconds), "unreachable"),
        (before(thresholds.stale_after_seconds), "stale"),
        (before(thresholds.warning_after_seconds), "warning"),
        else_="healthy",
    )


def health_state_expr(table: HealthThresholdTable, reference: datetime) -> ColumnElement[str]:
    """:func:`evaluate`'s state as a ``CASE``. Needs a *usable* reference.

    With none there is nothing to compute: callers return ``None`` rows and an
    empty filter result instead of building this.
    """
    return case(
        (Server.status == "RETIRED", "unknown"),
        (Server.operating_system != LINUX, "unknown"),
        (not_(Server.monitoring_enabled), "unknown"),
        (Server.metrics_instance.is_(None), "unknown"),
        *(
            (
                Server.environment == environment,
                _tier(table.for_environment(environment), reference),
            )
            for environment in ENVIRONMENTS
        ),
        else_=_tier(table.for_environment("default"), reference),
    )
