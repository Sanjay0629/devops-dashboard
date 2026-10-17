"""The "Prometheus last answered" marker (M2.5, D2).

One Redis key, written by the agent probe whenever Prometheus answered and read
by the status sweep and by the health read path. Hosts are aged against it
instead of the wall clock, so while Prometheus is down the clock stops and
nothing ages (PROGRESS issue 67).

Kept apart from ``sweep`` and ``read`` because both need it and neither should
import the other.

Everything unreadable — missing, garbage, naive, Redis down — is **no
reference**, never a time. A naive timestamp is refused rather than assumed to
be UTC: aging against a guessed zone is how a host gets called unreachable by
an hour.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final, Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MARKER_KEY: Final = "dcp:monitoring:prometheus_answered_at"

_SOCKET_TIMEOUT_SECONDS: Final = 2.0


class _Store(Protocol):
    def get(self, name: str) -> Any: ...
    def set(self, name: str, value: str) -> Any: ...


def redis_client(settings: Settings) -> Redis:
    """A short-deadline client; every outbound call has a timeout."""
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=_SOCKET_TIMEOUT_SECONDS,
    )


def parse_marker(stored: object) -> datetime | None:
    """A stored value as an aware instant, or ``None`` for anything else."""
    if not isinstance(stored, (bytes, str)):
        return None
    try:
        marker = datetime.fromisoformat(stored.decode() if isinstance(stored, bytes) else stored)
    except ValueError:
        return None
    return marker if marker.tzinfo is not None else None


def write_marker(client: _Store, at: datetime | None = None) -> None:
    """Record that Prometheus answered. Best effort: a failure is logged, not raised.

    A marker that fails to advance only makes the sweep more conservative, so
    it must never stop the probe from writing its own results.
    """
    try:
        client.set(MARKER_KEY, (at or datetime.now(UTC)).isoformat())
    except (RedisError, OSError):
        logger.warning("prometheus_marker_not_written")


def read_marker(client: _Store) -> datetime | None:
    """When Prometheus last answered, or ``None`` if that cannot be said."""
    try:
        stored = client.get(MARKER_KEY)
    except (RedisError, OSError):
        logger.warning("prometheus_marker_unreadable")
        return None
    return parse_marker(stored)
