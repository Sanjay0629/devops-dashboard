"""Query-bound guards for §19.1, checked before anything is rendered or sent.

Same shape as ``app/metrics/service.py``'s guards, and the same error: an
over-large request costs one comparison instead of a Loki round trip.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from app.core.errors import field_error
from app.metrics.service import QueryLimitExceededError

#: §19.1: "max range 7 days".
MAX_RANGE: Final = timedelta(days=7)
#: Servers a query may resolve to after scoping. A selector is a regex of UUIDs;
#: 200 of them measured fine against a real Loki (M3.2 spike). Over this is
#: refused, never silently truncated -- the caller would read a partial answer as
#: a complete one.
MAX_RESOLVED_SERVERS: Final = 200


def check_time_range(*, start: datetime, end: datetime, now: datetime, retention_days: int) -> None:
    """Order, span and retention, in that order.

    Retention is a refusal and not a clamp (``OPEN-37``): Loki would answer a
    range it no longer holds with an empty page, which during an incident reads
    as "no logs".
    """
    if start >= end:
        raise field_error(["body", "from"], "min_value", "'from' must be earlier than 'to'.")
    span = end - start
    if span > MAX_RANGE:
        raise QueryLimitExceededError(
            "range_seconds", MAX_RANGE.total_seconds(), span.total_seconds()
        )
    retention = timedelta(days=retention_days)
    if now - start > retention:
        raise QueryLimitExceededError(
            "retention_seconds", retention.total_seconds(), (now - start).total_seconds()
        )


def check_resolved_servers(count: int) -> None:
    if count > MAX_RESOLVED_SERVERS:
        raise QueryLimitExceededError("servers", MAX_RESOLVED_SERVERS, count)
