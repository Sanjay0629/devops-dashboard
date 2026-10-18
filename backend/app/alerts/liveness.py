"""What DCP knows about the alerting pipeline itself, kept in Redis (``OPEN-47``).

PRD §3: DCP must not report a server healthy merely because no alert arrived, and the
mirror of that is that it must not report the alerting pipeline healthy merely because
nothing is wrong. Three facts answer it:

* **last delivery** -- when an authenticated Alertmanager last called the webhook;
* **last watchdog** -- when the always-firing ``DcpWatchdog`` rule last arrived, which
  proves rule evaluation *and* delivery, not just that someone can reach the endpoint;
* **refusals** -- how many calls were turned away and why, per day. Smoke asserts these
  counters, because "nothing stored" alone is also what a dead endpoint looks like.

Best effort by design: a Redis failure while *recording* must never cost an alert, so
every write swallows and logs ``RedisError``; reads report ``None`` ("unknown"), never a
fabricated time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_PREFIX: Final = "dcp:alerts:"
LAST_DELIVERY_KEY: Final = f"{_PREFIX}last_delivery"
LAST_WATCHDOG_KEY: Final = f"{_PREFIX}last_watchdog"
#: Refusal reasons, closed so a counter name is never built from request text.
REFUSAL_REASONS: Final[tuple[str, ...]] = (
    "not_configured",
    "source_address",
    "rate_limited",
    "bad_secret",
    "body_too_large",
    "invalid_body",
)
_TWO_DAYS: Final = 2 * 24 * 3600
#: How long the two timestamps are kept: far beyond any threshold that reads them.
_KEEP: Final = 7 * 24 * 3600


def _day() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def refusal_key(reason: str, day: str | None = None) -> str:
    return f"{_PREFIX}refused:{reason}:{day or _day()}"


async def _set_now(key: str) -> None:
    try:
        await get_redis().set(key, datetime.now(UTC).isoformat(), ex=_KEEP)
    except RedisError as error:
        logger.warning("alert_liveness_not_recorded", key=key, error=str(error))


async def note_delivery() -> None:
    await _set_now(LAST_DELIVERY_KEY)


async def note_watchdog() -> None:
    await _set_now(LAST_WATCHDOG_KEY)


async def note_refused(reason: str) -> None:
    if reason not in REFUSAL_REASONS:  # a programming error, not a request's business
        raise ValueError(f"unknown refusal reason {reason!r}")
    try:
        client = get_redis()
        key = refusal_key(reason)
        await client.incr(key)
        await client.expire(key, _TWO_DAYS)
    except RedisError as error:
        logger.warning("alert_refusal_not_counted", reason=reason, error=str(error))


async def _read_time(key: str) -> datetime | None:
    value = await get_redis().get(key)
    return datetime.fromisoformat(value) if value else None


async def read() -> dict[str, object] | None:
    """The three facts, or ``None`` when Redis cannot answer (unknown, not "never")."""
    try:
        client = get_redis()
        refused = {
            reason: int(await client.get(refusal_key(reason)) or 0) for reason in REFUSAL_REASONS
        }
        return {
            "last_delivery_at": await _read_time(LAST_DELIVERY_KEY),
            "last_watchdog_at": await _read_time(LAST_WATCHDOG_KEY),
            "refused_today": refused,
        }
    except RedisError as error:
        logger.warning("alert_liveness_unreadable", error=str(error))
        return None
